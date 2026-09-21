import asyncio
from datetime import date
from types import SimpleNamespace

import orchestrator
from orchestrator import PlanRequest, plan

REQ = PlanRequest("Madrid", "Paris", date(2026, 10, 10), date(2026, 10, 14), 1, 1000.0, "USD", "")


def setup(monkeypatch, costs):
    """Fake agents: each call pops the next cost for that kind. Records (kind, cap) per call."""
    calls = []

    async def run_specialist(kind, req, cap):
        calls.append((kind, cap))
        return SimpleNamespace(total=costs[kind].pop(0))

    async def write_itinerary(req, results, over_budget):
        return "# plan"

    monkeypatch.setattr(orchestrator, "run_specialist", run_specialist)
    monkeypatch.setattr(orchestrator, "write_itinerary", write_itinerary)
    return calls


def test_under_budget_runs_each_agent_once(monkeypatch):
    calls = setup(monkeypatch, {"flight": [400], "stay": [300], "activity": [200]})
    result = asyncio.run(plan(REQ))
    assert [k for k, _ in calls] == ["flight", "stay", "activity"]
    assert result.totals == {"flight": 400, "stay": 300, "activity": 200}
    assert result.caps == {"flight": 400, "stay": 350, "activity": 250}
    assert result.over_budget is False
    assert result.itinerary_md == "# plan"


def test_over_budget_reruns_worst_offender_with_reduced_cap(monkeypatch):
    # flight cap 400 -> 600 (worst, over by 200). Total 1100 vs budget 1000 -> overage 100 -> new flight cap 300.
    calls = setup(monkeypatch, {"flight": [600, 380], "stay": [300], "activity": [200]})
    result = asyncio.run(plan(REQ))
    assert calls[3] == ("flight", 300)
    assert len(calls) == 4
    assert result.totals["flight"] == 380
    assert result.over_budget is False


def test_gives_up_after_max_retries(monkeypatch):
    calls = setup(monkeypatch, {"flight": [900, 900, 900], "stay": [300], "activity": [200]})
    events = []
    result = asyncio.run(plan(REQ, on_event=lambda k, s, d: events.append((k, s))))
    assert len(calls) == 5
    assert calls[4][1] == 0  # cap clamped at zero, never negative
    assert result.over_budget is True
    assert events.count(("flight", "retry")) == 2
    assert events[-1] == ("writer", "done")
