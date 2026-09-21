import json

from fastapi.testclient import TestClient

import api
import db
from orchestrator import PlanResult


def fake_plan(events):
    async def plan(req, on_event):
        for kind, status, detail in events:
            on_event(kind, status, detail)
        return PlanResult("# itinerary", {"flight": 400.0, "stay": 300.0, "activity": 200.0}, {}, False)
    return plan


def sse_events(text):
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


PARAMS = dict(origin="Madrid", destination="Paris", depart="2026-10-10", return_date="2026-10-14",
              travelers=1, budget=1000, currency="USD", preferences="museums")


def set_keys(monkeypatch):
    for k in ("OPENAI_API_KEY", "SERPAPI_API_KEY"):
        monkeypatch.setenv(k, "x")


def test_plan_streams_events_then_result_and_saves(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(api, "plan", fake_plan([("flight", "start", "cap 400 USD"), ("flight", "done", "380 USD")]))
    set_keys(monkeypatch)
    client = TestClient(api.app)

    r = client.get("/api/plan", params=PARAMS)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert events[0] == {"kind": "flight", "status": "start", "detail": "cap 400 USD"}
    assert events[1] == {"kind": "flight", "status": "done", "detail": "380 USD"}
    assert events[2]["kind"] == "plan" and events[2]["status"] == "done"
    assert events[2]["itinerary"] == "# itinerary" and events[2]["over_budget"] is False

    plans = client.get("/api/plans").json()
    assert len(plans) == 1 and plans[0]["title"] == "Madrid → Paris 2026-10-10" and plans[0]["total"] == 900.0
    assert client.get(f"/api/plans/{plans[0]['id']}").json() == {"itinerary": "# itinerary"}


def test_plan_error_is_streamed_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")

    async def boom(req, on_event):
        raise ValueError("Amadeus does not know a city called 'Atlantis'")

    monkeypatch.setattr(api, "plan", boom)
    set_keys(monkeypatch)
    r = TestClient(api.app).get("/api/plan", params=PARAMS)
    assert sse_events(r.text) == [{"kind": "plan", "status": "error", "detail": "Amadeus does not know a city called 'Atlantis'"}]


def test_plan_rejects_bad_input(monkeypatch):
    set_keys(monkeypatch)
    client = TestClient(api.app)
    assert client.get("/api/plan", params={**PARAMS, "return_date": "2026-10-09"}).status_code == 422
    assert client.get("/api/plan", params={**PARAMS, "currency": "EUR"}).status_code == 422
    assert client.get("/api/plan", params={**PARAMS, "travelers": 0}).status_code == 422


def test_plan_reports_missing_keys(monkeypatch):
    set_keys(monkeypatch)
    monkeypatch.delenv("SERPAPI_API_KEY")
    r = TestClient(api.app).get("/api/plan", params=PARAMS)
    assert r.status_code == 503 and "SERPAPI_API_KEY" in r.json()["detail"]
