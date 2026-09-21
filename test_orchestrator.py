import asyncio
from datetime import date
from types import SimpleNamespace

import orchestrator
from orchestrator import PlanRequest, plan, reconcile

REQ = PlanRequest("Madrid", "Paris", date(2026, 10, 10), date(2026, 10, 14), 1, 1000.0, "USD", "")


def setup(monkeypatch, costs):
    """Fake agents: each call pops the next cost for that kind. Records (kind, cap) per call."""
    calls = []

    async def run_specialist(kind, req, cap):
        calls.append((kind, cap))
        total = costs[kind].pop(0)
        return SimpleNamespace(total=total, model_dump=lambda: {"total": total, "items": []})

    async def write_itinerary(req, results, over_budget):
        return {"title": "plan", "days": []}

    monkeypatch.setattr(orchestrator, "run_specialist", run_specialist)
    monkeypatch.setattr(orchestrator, "write_itinerary", write_itinerary)
    return calls


def test_under_budget_runs_each_agent_once(monkeypatch):
    calls = setup(monkeypatch, {"flight": [400], "stay": [300], "activity": [200]})
    result = asyncio.run(plan(REQ))
    assert [k for k, _ in calls] == ["flight", "stay", "activity"]
    assert result.totals == {"flight": 400, "stay": 300, "activity": 200}
    assert result.caps == {"flight": 350, "stay": 300, "activity": 350}
    assert result.over_budget is False
    assert result.plan["itinerary"] == {"title": "plan", "days": []}
    assert result.plan["flight"]["total"] == 400 and result.plan["request"]["depart"] == "2026-10-10"


def test_over_budget_reruns_worst_offender_with_reduced_cap(monkeypatch):
    # flight cap 350 -> 600 (worst, over by 250). Total 1100 vs budget 1000 -> overage 100 -> new flight cap 250.
    calls = setup(monkeypatch, {"flight": [600, 380], "stay": [300], "activity": [200]})
    result = asyncio.run(plan(REQ))
    assert calls[3] == ("flight", 250)
    assert len(calls) == 4
    assert result.totals["flight"] == 380
    assert result.over_budget is False


def test_stops_early_when_retry_cannot_do_better(monkeypatch):
    calls = setup(monkeypatch, {"flight": [900, 900, 900], "stay": [300], "activity": [200]})
    events = []
    result = asyncio.run(plan(REQ, on_event=lambda k, s, d: events.append((k, s))))
    assert len(calls) == 4  # one retry, then stop: same price back means the cap is not the constraint
    assert calls[3][1] == 0  # cap clamped at zero, never negative
    assert result.over_budget is True
    assert events.count(("flight", "retry")) == 1
    assert events[-1] == ("writer", "done")


def test_gives_up_after_max_retries(monkeypatch):
    calls = setup(monkeypatch, {"flight": [900, 800, 700], "stay": [300], "activity": [200]})
    result = asyncio.run(plan(REQ))
    assert len(calls) == 5  # each retry improved, so both were used
    assert result.totals["flight"] == 700
    assert result.over_budget is True


def test_reconcile_places_dropped_items_and_dedupes():
    items = [{"time_of_day": "afternoon"}, {"time_of_day": "evening"}, {"time_of_day": "morning"}]
    itinerary = {"days": [
        {"day": 1, "slots": [{"time_of_day": "evening", "notes": [], "items": [1, 1]}]},          # duplicate ref
        {"day": 2, "slots": [{"time_of_day": "evening", "notes": [], "items": []}]},
        {"day": 3, "slots": [{"time_of_day": "morning", "notes": [], "items": [7]}]},             # bogus index
        {"day": 4, "slots": []},                                                                 # travel day
    ]}
    out = reconcile(itinerary, items)
    assert out["days"][0]["slots"][0]["items"] == [1]  # duplicate dropped
    # items 0 and 2 were unplaced -> spread over the lightest non-travel days, each in its own time-of-day slot, slots sorted
    assert [(sl["time_of_day"], sl["items"]) for sl in out["days"][1]["slots"]] == [("afternoon", [0]), ("evening", [])]
    assert [(sl["time_of_day"], sl["items"]) for sl in out["days"][2]["slots"]] == [("morning", [2])]
    assert out["days"][3]["slots"] == []  # travel day untouched
    used = sorted(i for d in out["days"] for sl in d["slots"] for i in sl["items"])
    assert used == [0, 1, 2]
