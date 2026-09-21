"""Deterministic coordinator: split budget, run specialists in parallel, validate, retry, write."""
import asyncio
from dataclasses import dataclass
from datetime import date

from agents import Runner

from specialists import activity_agent, flight_agent, stay_agent, writer_agent

SPLIT = {"flight": 0.35, "stay": 0.30, "activity": 0.35}  # activity = sights, experiences, dining, nightlife
MAX_RETRIES = 2
AGENTS = {"flight": flight_agent, "stay": stay_agent, "activity": activity_agent}


@dataclass
class PlanRequest:
    origin: str
    destination: str
    depart: date
    return_date: date
    travelers: int
    budget: float
    currency: str
    preferences: str


@dataclass
class PlanResult:
    itinerary_md: str
    totals: dict[str, float]
    caps: dict[str, float]
    over_budget: bool


def _brief(req: PlanRequest, cap: float) -> str:
    return (
        f"Trip: {req.origin} -> {req.destination}.\n"
        f"Dates: depart {req.depart}, return {req.return_date} ({(req.return_date - req.depart).days} nights). Travelers: {req.travelers}.\n"
        f"Currency: {req.currency}. Your budget cap: {cap:.0f} {req.currency}.\n"
        f"Traveler preferences: {req.preferences.strip() or 'none given'}."
    )


async def run_specialist(kind: str, req: PlanRequest, cap: float):
    return (await Runner.run(AGENTS[kind], _brief(req, cap))).final_output


async def write_itinerary(req: PlanRequest, results: dict, over_budget: bool) -> str:
    prompt = (
        _brief(req, req.budget).replace("Your budget cap", "Total budget")
        + f"\nOver budget: {'yes' if over_budget else 'no'}.\n\nSpecialist picks (JSON):\n"
        + "\n".join(f"{k}: {r.model_dump_json()}" for k, r in results.items())
    )
    return (await Runner.run(writer_agent, prompt)).final_output


async def plan(req: PlanRequest, on_event=None) -> PlanResult:
    on_event = on_event or (lambda kind, status, detail: None)
    caps = {k: req.budget * share for k, share in SPLIT.items()}

    async def run(kind):
        on_event(kind, "start", f"cap {caps[kind]:.0f} {req.currency}")
        r = await run_specialist(kind, req, caps[kind])
        on_event(kind, "done", f"{r.total:.0f} {req.currency}")
        return r

    results = dict(zip(SPLIT, await asyncio.gather(*(run(k) for k in SPLIT))))

    for _ in range(MAX_RETRIES):
        total = sum(r.total for r in results.values())
        if total <= req.budget:
            break
        overage = total - req.budget
        worst = max(results, key=lambda k: results[k].total - caps[k])
        caps[worst] = max(caps[worst] - overage, 0)
        on_event(worst, "retry", f"plan over by {overage:.0f} {req.currency}; new cap {caps[worst]:.0f}")
        before = results[worst].total
        results[worst] = await run(worst)
        if results[worst].total >= before:  # the market floor, not the cap, is the limit; retrying again won't help
            break

    totals = {k: r.total for k, r in results.items()}
    over_budget = sum(totals.values()) > req.budget
    on_event("writer", "start", "")
    md = await write_itinerary(req, results, over_budget)
    on_event("writer", "done", "")
    return PlanResult(md, totals, caps, over_budget)
