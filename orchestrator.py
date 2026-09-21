"""Deterministic coordinator: split budget, run specialists in parallel, validate, retry, write."""
import asyncio
import json
from dataclasses import asdict, dataclass
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
    plan: dict  # request, totals, caps, over_budget, flight, stay, activity, itinerary - everything the UI renders
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


async def write_itinerary(req: PlanRequest, results: dict, over_budget: bool) -> dict:
    act = results["activity"]
    items = "\n".join(
        f"{i}: {json.dumps({k: v for k, v in it.model_dump().items() if k not in ('image', 'link', 'description')})}"
        for i, it in enumerate(act.items)
    )
    prompt = (
        _brief(req, req.budget).replace("Your budget cap", "Total budget")
        + f"\nOver budget: {'yes' if over_budget else 'no'}.\n"
        + f"\nFlight: {results['flight'].model_dump_json(exclude={'image'})}"
        + f"\nStay: {results['stay'].model_dump_json(exclude={'image'})}"
        + f"\nExperience plan: total {act.total}, estimated_total {act.estimated_total}, currency {act.currency}. Reason: {act.reason}"
        + f"\nExperience items (reference by index):\n{items or '(none)'}"
    )
    return (await Runner.run(writer_agent, prompt)).final_output.model_dump()


def dedupe_items(act):
    """Keep the first occurrence of each place; the agent sometimes lists the same beach on three days."""
    seen: set[str] = set()
    kept = []
    for it in act.items:
        key = it.name.strip().lower()
        if key in seen:
            act.total -= it.price
            if it.estimated:
                act.estimated_total -= it.price
            continue
        seen.add(key)
        kept.append(it)
    act.items = kept
    return act


_SLOT_ORDER = {"morning": 0, "afternoon": 1, "evening": 2}


def reconcile(itinerary: dict, items: list[dict]) -> dict:
    """Guarantee every experience item appears exactly once: drop duplicate references, then put any item the
    writer left out on the lightest non-travel day (the writer may have dropped it for a good timing reason,
    so we don't force it back onto its original day). Slots end up in morning/afternoon/evening order."""
    days = itinerary["days"]
    seen: set[int] = set()
    for d in days:
        for sl in d["slots"]:
            sl["items"] = [i for i in sl["items"] if 0 <= i < len(items) and not (i in seen or seen.add(i))]
    candidates = days[1:-1] if len(days) > 2 else days
    for i, it in enumerate(items):
        if i in seen or not candidates:
            continue
        day = min(candidates, key=lambda d: sum(len(sl["items"]) for sl in d["slots"]))
        slot = next((sl for sl in day["slots"] if sl["time_of_day"] == it["time_of_day"]), None)
        if slot is None:
            slot = {"time_of_day": it["time_of_day"], "notes": [], "items": []}
            day["slots"].append(slot)
        slot["items"].append(i)
        seen.add(i)
    for d in days:
        d["slots"].sort(key=lambda sl: _SLOT_ORDER.get(sl["time_of_day"], 9))
    return itinerary


async def plan(req: PlanRequest, on_event=None) -> PlanResult:
    on_event = on_event or (lambda kind, status, detail: None)
    caps = {k: req.budget * share for k, share in SPLIT.items()}

    async def run(kind):
        on_event(kind, "start", f"cap {caps[kind]:.0f} {req.currency}")
        r = await run_specialist(kind, req, caps[kind])
        if kind == "activity":
            r = dedupe_items(r)
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
        previous = results[worst]
        retried = await run(worst)
        if retried.total <= 0 < previous.total:  # agent gave up rather than exceed the cap: keep the real pick, stop retrying
            on_event(worst, "done", f"{previous.total:.0f} {req.currency} (kept: no cheaper option)")
            break
        results[worst] = retried
        if retried.total >= previous.total:  # the market floor, not the cap, is the limit; retrying again won't help
            break

    totals = {k: r.total for k, r in results.items()}
    over_budget = sum(totals.values()) > req.budget
    on_event("writer", "start", "")
    itinerary = reconcile(await write_itinerary(req, results, over_budget), results["activity"].model_dump()["items"])
    on_event("writer", "done", "")
    plan_dict = {
        "request": {**asdict(req), "depart": req.depart.isoformat(), "return_date": req.return_date.isoformat()},
        "totals": totals, "caps": caps, "over_budget": over_budget,
        **{k: r.model_dump() for k, r in results.items()},
        "itinerary": itinerary,
    }
    return PlanResult(plan_dict, totals, caps, over_budget)
