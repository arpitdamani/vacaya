# Vacaya Multi-Agent Travel Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A web app (React UI built from 21st.dev components, FastAPI backend) where a user enters origin, destination, dates, travelers, budget (USD/INR) and preferences, and three parallel specialist agents (flights, stay, activities) backed by Amadeus produce typed picks that a deterministic orchestrator validates against the budget (retrying over-budget agents) before a writer agent renders a day-by-day itinerary, saved to SQLite.

**Architecture:** `orchestrator.plan()` is plain async Python: resolve cities via Amadeus, split budget 40/35/25, `asyncio.gather` three OpenAI Agents SDK agents (each with one Amadeus `function_tool` and a Pydantic `output_type`), sum `.total`, re-run the worst offender with a reduced cap up to 2 times, then call a tool-less writer agent for Markdown. `api.py` exposes it as an SSE stream (one event per `on_event` call, then the itinerary) and persists results through `db.py`; `web/` is a Vite + React + Tailwind + shadcn app using 21st.dev's Booking Form and AI Task List components, served from `web/dist` by FastAPI in one Docker container on Hugging Face Spaces.

**Tech Stack:** Python 3.14, openai-agents 0.22, amadeus 12, fastapi + uvicorn, python-dotenv, pytest, sqlite3 (stdlib), urllib (stdlib) for frankfurter.app FX; Node 24, Vite, React 19, TypeScript, Tailwind 4, shadcn, 21st.dev components (via the 21st.dev MCP connector), react-markdown; Docker; Hugging Face Spaces.

## Global Constraints

> **Amendment (2026-09-21, after Task 6):** Amadeus Self-Service was decommissioned on 2026-07-17, so Task 1's tools were
> replaced by SerpApi (`google_flights`, `google_hotels`, `google` → `top_sights`) and city resolution was removed from the
> orchestrator. Secrets are now `OPENAI_API_KEY` and `SERPAPI_API_KEY`. The code and spec are the source of truth; the
> Amadeus snippets below are historical.

- Module for agents is `specialists.py`, never `agents.py` (collides with the SDK package).
- Every specialist output model has a `total: float` field — the orchestrator's budget math depends on it.
- Tools return JSON strings; on Amadeus error they return `{"error": "..."}`; never fabricated data.
- Model is `os.getenv("OPENAI_MODEL")` → `None` means SDK default. Same for all agents.
- Currency is `USD` or `INR`; flights/hotels request it via Amadeus; activity prices converted in-tool via frankfurter.app, falling back to source currency when the rate fetch fails.
- Budget split constants: flight 0.40, stay 0.35, activity 0.25. `MAX_RETRIES = 2`.
- Tests never hit OpenAI or Amadeus.
- Commit after every task with the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer.
- Venv already exists at `.venv`; run Python as `.venv/Scripts/python` and tests as `.venv/Scripts/python -m pytest`. Install new Python deps with `.venv/Scripts/python -m pip install fastapi uvicorn httpx` before Task 4.
- Frontend lives in `web/`; 21st.dev component code is fetched with the 21st.dev MCP `get_component` tool (ids 7508 and 23793), never hand-copied from the website.
- Container listens on port 7860 (Hugging Face Spaces requirement).

---

### Task 1: Project scaffold + specialists (models, Amadeus tools, agents)

**Files:**
- Create: `requirements.txt`, `.env.example`, `.gitignore`
- Create: `specialists.py`
- Test: `test_specialists.py`

**Interfaces:**
- Produces: `City(code, name, lat, lon)` pydantic model; `resolve_city(name: str) -> City`; `fx_rate(src: str, dst: str) -> float | None`; plain functions `_search_flights`, `_search_hotels`, `_search_activities` (each `-> str` JSON) and their `function_tool` wrappers `search_flights`, `search_hotels`, `search_activities`; models `FlightPick`, `StayPick`, `ActivityItem`, `ActivityPlan` (all with `total: float` except `ActivityItem`); agents `flight_agent`, `stay_agent`, `activity_agent`, `writer_agent`; `amadeus()` client accessor (monkeypatch target).

- [ ] **Step 1: Scaffold files**

`requirements.txt`:
```
openai-agents
amadeus
fastapi
uvicorn
python-dotenv
pytest
httpx
```

`.env.example`:
```
OPENAI_API_KEY=sk-...
AMADEUS_CLIENT_ID=...
AMADEUS_CLIENT_SECRET=...
# optional; unset = SDK default model
# OPENAI_MODEL=gpt-4.1-mini
```

`.gitignore`:
```
.venv/
.env
plans.db
__pycache__/
.pytest_cache/
web/node_modules/
web/dist/
```

- [ ] **Step 2: Write the failing tests**

`test_specialists.py`:
```python
import json
from types import SimpleNamespace

import specialists
from specialists import _search_activities, _search_flights, _search_hotels


def fake_amadeus(monkeypatch, **responses):
    """Build a stub with the same attribute paths the tools use; each leaf .get() returns .data."""
    def leaf(data):
        return SimpleNamespace(get=lambda **kw: SimpleNamespace(data=data))
    stub = SimpleNamespace(
        shopping=SimpleNamespace(
            flight_offers_search=leaf(responses.get("flights", [])),
            hotel_offers_search=leaf(responses.get("hotel_offers", [])),
            activities=leaf(responses.get("activities", [])),
        ),
        reference_data=SimpleNamespace(
            locations=SimpleNamespace(hotels=SimpleNamespace(by_city=leaf(responses.get("hotels", []))))
        ),
    )
    monkeypatch.setattr(specialists, "amadeus", lambda: stub)


def test_search_flights_reshapes_offer(monkeypatch):
    seg = lambda dep, arr, c: {"departure": {"at": dep}, "arrival": {"at": arr}, "carrierCode": c}
    fake_amadeus(monkeypatch, flights=[{
        "price": {"grandTotal": "123.45", "currency": "USD"},
        "itineraries": [
            {"segments": [seg("2026-10-10T08:00", "2026-10-10T10:00", "IB"), seg("2026-10-10T11:00", "2026-10-10T13:00", "AF")]},
            {"segments": [seg("2026-10-14T18:00", "2026-10-14T20:00", "IB")]},
        ],
    }])
    out = json.loads(_search_flights("MAD", "PAR", "2026-10-10", "2026-10-14", 1, "USD"))
    assert out == [{
        "total": 123.45, "currency": "USD",
        "outbound": {"depart_at": "2026-10-10T08:00", "arrive_at": "2026-10-10T13:00", "stops": 1, "carriers": ["AF", "IB"]},
        "inbound": {"depart_at": "2026-10-14T18:00", "arrive_at": "2026-10-14T20:00", "stops": 0, "carriers": ["IB"]},
    }]


def test_search_hotels_computes_nightly(monkeypatch):
    fake_amadeus(monkeypatch,
        hotels=[{"hotelId": "H1"}],
        hotel_offers=[{"hotel": {"name": "Hotel A", "hotelId": "H1"},
                       "offers": [{"price": {"total": "400.00", "currency": "USD"},
                                   "room": {"description": {"text": "Double room"}}}]}])
    out = json.loads(_search_hotels("PAR", "2026-10-10", "2026-10-14", 1, "USD"))
    assert out == [{"name": "Hotel A", "hotel_id": "H1", "total": 400.0, "nightly": 100.0, "currency": "USD", "room": "Double room"}]


def test_search_activities_converts_currency_and_skips_unpriced(monkeypatch):
    fake_amadeus(monkeypatch, activities=[
        {"name": "Louvre", "shortDescription": "Art", "price": {"amount": "20.0", "currencyCode": "EUR"}, "rating": "4.5"},
        {"name": "Free walk", "price": {}},
    ])
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: 2.0)
    out = json.loads(_search_activities(48.85, 2.35, "USD"))
    assert out == [{"name": "Louvre", "description": "Art", "price": 40.0, "currency": "USD", "rating": "4.5"}]


def test_search_activities_keeps_source_currency_when_fx_fails(monkeypatch):
    fake_amadeus(monkeypatch, activities=[{"name": "X", "price": {"amount": "5", "currencyCode": "EUR"}}])
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: None)
    out = json.loads(_search_activities(0, 0, "INR"))
    assert out[0]["price"] == 5.0 and out[0]["currency"] == "EUR"


def test_tools_have_expected_names():
    assert specialists.search_flights.name == "search_flights"
    assert specialists.search_hotels.name == "search_hotels"
    assert specialists.search_activities.name == "search_activities"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test_specialists.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'specialists'`

- [ ] **Step 4: Write `specialists.py`**

```python
"""Amadeus-backed tools, typed outputs and the four agents. Orchestration lives in orchestrator.py."""
import json
import os
from datetime import date
from functools import lru_cache
from urllib.request import urlopen

from agents import Agent, function_tool
from amadeus import Client, Location, ResponseError
from pydantic import BaseModel

MODEL = os.getenv("OPENAI_MODEL")  # None -> SDK default

_client = None


def amadeus() -> Client:
    global _client
    if _client is None:
        _client = Client(client_id=os.environ["AMADEUS_CLIENT_ID"], client_secret=os.environ["AMADEUS_CLIENT_SECRET"])
    return _client


# ---------- shared helpers (called by the orchestrator, not by agents) ----------

class City(BaseModel):
    code: str
    name: str
    lat: float
    lon: float


def resolve_city(name: str) -> City:
    data = amadeus().reference_data.locations.get(keyword=name, subType=Location.CITY).data
    if not data:
        raise ValueError(f"Amadeus does not know a city called {name!r}")
    d = data[0]
    return City(code=d["iataCode"], name=d["name"], lat=d["geoCode"]["latitude"], lon=d["geoCode"]["longitude"])


@lru_cache
def fx_rate(src: str, dst: str) -> float | None:
    if src == dst:
        return 1.0
    try:
        with urlopen(f"https://api.frankfurter.app/latest?from={src}&to={dst}", timeout=10) as r:
            return float(json.load(r)["rates"][dst])
    except Exception:
        return None


# ---------- tools ----------

def _search_flights(origin: str, destination: str, depart: str, return_date: str, adults: int, currency: str) -> str:
    """Search the cheapest round-trip flight offers. Returns a JSON list sorted by price, each with total price for all travelers, outbound and inbound legs (times, stops, carriers).

    Args:
        origin: IATA city or airport code, e.g. MAD
        destination: IATA city or airport code, e.g. PAR
        depart: outbound date YYYY-MM-DD
        return_date: return date YYYY-MM-DD
        adults: number of travelers
        currency: ISO currency code, USD or INR
    """
    try:
        offers = amadeus().shopping.flight_offers_search.get(
            originLocationCode=origin, destinationLocationCode=destination,
            departureDate=depart, returnDate=return_date, adults=adults,
            currencyCode=currency, max=10,
        ).data
    except ResponseError as e:
        return json.dumps({"error": str(e)})

    def leg(itinerary):
        segs = itinerary["segments"]
        return {
            "depart_at": segs[0]["departure"]["at"],
            "arrive_at": segs[-1]["arrival"]["at"],
            "stops": len(segs) - 1,
            "carriers": sorted({s["carrierCode"] for s in segs}),
        }

    return json.dumps([{
        "total": float(o["price"]["grandTotal"]),
        "currency": o["price"]["currency"],
        "outbound": leg(o["itineraries"][0]),
        "inbound": leg(o["itineraries"][1]),
    } for o in offers])


def _search_hotels(city_code: str, check_in: str, check_out: str, adults: int, currency: str) -> str:
    """Search hotel offers in a city for the whole stay. Returns a JSON list with hotel name, total price for the stay, nightly price and room description.

    Args:
        city_code: IATA city code, e.g. PAR
        check_in: check-in date YYYY-MM-DD
        check_out: check-out date YYYY-MM-DD
        adults: number of guests
        currency: ISO currency code, USD or INR
    """
    try:
        hotels = amadeus().reference_data.locations.hotels.by_city.get(cityCode=city_code).data[:20]
        if not hotels:
            return json.dumps([])
        offers = amadeus().shopping.hotel_offers_search.get(
            hotelIds=",".join(h["hotelId"] for h in hotels),
            checkInDate=check_in, checkOutDate=check_out, adults=adults,
            currency=currency, bestRateOnly=True,
        ).data
    except ResponseError as e:
        return json.dumps({"error": str(e)})
    nights = max((date.fromisoformat(check_out) - date.fromisoformat(check_in)).days, 1)
    out = []
    for h in offers:
        for o in h.get("offers", []):
            total = float(o["price"]["total"])
            out.append({
                "name": h["hotel"]["name"],
                "hotel_id": h["hotel"]["hotelId"],
                "total": total,
                "nightly": round(total / nights, 2),
                "currency": o["price"]["currency"],
                "room": (o.get("room", {}).get("description", {}).get("text") or "")[:120],
            })
    return json.dumps(out)


def _search_activities(lat: float, lon: float, currency: str) -> str:
    """Find bookable tours and activities within 10 km of a point. Returns a JSON list with name, description, price, currency and rating. Prices are converted to `currency` when an exchange rate is available; otherwise the item keeps its original currency.

    Args:
        lat: latitude of the city centre
        lon: longitude of the city centre
        currency: ISO currency code the traveler budgets in, USD or INR
    """
    try:
        acts = amadeus().shopping.activities.get(latitude=lat, longitude=lon, radius=10).data
    except ResponseError as e:
        return json.dumps({"error": str(e)})
    out = []
    for a in acts:
        price = a.get("price") or {}
        if not price.get("amount"):
            continue
        amount, cur = float(price["amount"]), price.get("currencyCode", "EUR")
        rate = fx_rate(cur, currency)
        if rate:
            amount, cur = round(amount * rate, 2), currency
        out.append({
            "name": a["name"],
            "description": (a.get("shortDescription") or "")[:160],
            "price": amount,
            "currency": cur,
            "rating": a.get("rating"),
        })
    return json.dumps(out[:40])


search_flights = function_tool(_search_flights, name_override="search_flights")
search_hotels = function_tool(_search_hotels, name_override="search_hotels")
search_activities = function_tool(_search_activities, name_override="search_activities")


# ---------- typed outputs ----------

class FlightPick(BaseModel):
    airline: str
    depart_at: str
    return_at: str
    stops: int
    total: float
    reason: str


class StayPick(BaseModel):
    name: str
    room: str
    nightly: float
    total: float
    reason: str


class ActivityItem(BaseModel):
    name: str
    price: float
    day: int  # 1-based day of the trip


class ActivityPlan(BaseModel):
    items: list[ActivityItem]
    total: float
    currency: str
    reason: str


# ---------- agents ----------

_COMMON = """You are one specialist in a team planning a trip. You receive the trip details, the traveler's preferences and a budget cap for your part.
Call your search tool once, then pick the single best option that fits the preferences and stays within the cap. Prefer the cheapest option that still matches the preferences.
If the tool returns an error or no options, do not invent anything: set total to 0, leave other fields empty or 0, and explain in `reason`. If every option exceeds the cap, pick the cheapest and say so in `reason`.
Write `reason` as one or two sentences addressed to the traveler explaining why this pick matches their preferences. """

flight_agent = Agent(
    name="Flight agent",
    instructions=_COMMON + "You handle round-trip flights. `airline` is the carrier code(s), `depart_at`/`return_at` are the outbound and inbound departure times.",
    tools=[search_flights],
    output_type=FlightPick,
    model=MODEL,
)

stay_agent = Agent(
    name="Stay agent",
    instructions=_COMMON + "You handle the hotel for the whole stay. `total` is the price for all nights.",
    tools=[search_hotels],
    output_type=StayPick,
    model=MODEL,
)

activity_agent = Agent(
    name="Activity agent",
    instructions=_COMMON + "You handle things to do. Pick 1-3 activities per day spread across the trip (`day` is 1-based), matching the preferences, keeping the sum of prices within the cap. `total` must equal the sum of item prices. `currency` is the currency of the prices you used.",
    tools=[search_activities],
    output_type=ActivityPlan,
    model=MODEL,
)

writer_agent = Agent(
    name="Itinerary writer",
    instructions="""You write the final travel itinerary in Markdown from the specialists' picks. Structure:
1. A title line and a one-paragraph overview.
2. A cost table with rows Flights, Stay, Activities, Total, Budget, Difference, in the given currency.
3. One section per day (`## Day N - <date>`) listing flights on arrival/departure days, hotel check-in/out, and the activities assigned to that day. For days with no activity, add one short suggestion consistent with the preferences, labelled "Suggestion" with no price.
4. A "Why these picks" section quoting each specialist's reason.
If a section reports no options found, say so plainly and do not invent prices. If the plan is over budget, say so in the first line with the amount.""",
    model=MODEL,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest test_specialists.py -q`
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore specialists.py test_specialists.py
git commit -m "Add specialist agents with Amadeus tools

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Orchestrator with budget retry loop

**Files:**
- Create: `orchestrator.py`
- Test: `test_orchestrator.py`

**Interfaces:**
- Consumes: `specialists.resolve_city`, `specialists.City`, `specialists.flight_agent/stay_agent/activity_agent/writer_agent`, `agents.Runner`.
- Produces: `PlanRequest(origin, destination, depart: date, return_date: date, travelers: int, budget: float, currency: str, preferences: str)` dataclass; `PlanResult(itinerary_md: str, totals: dict[str, float], caps: dict[str, float], over_budget: bool)` dataclass; `async plan(req: PlanRequest, on_event=None) -> PlanResult` where `on_event(kind: str, status: str, detail: str)` and `kind ∈ {"flight","stay","activity","writer"}`, `status ∈ {"start","done","retry"}`; module-level `run_specialist(kind, req, cities, cap)` and `write_itinerary(req, cities, results, over_budget)` coroutines (monkeypatch targets); constants `SPLIT`, `MAX_RETRIES`.

- [ ] **Step 1: Write the failing tests**

`test_orchestrator.py`:
```python
import asyncio
from datetime import date
from types import SimpleNamespace

import orchestrator
from orchestrator import PlanRequest, plan

REQ = PlanRequest("Madrid", "Paris", date(2026, 10, 10), date(2026, 10, 14), 1, 1000.0, "USD", "")


def setup(monkeypatch, costs):
    """Fake agents: each call pops the next cost for that kind. Records (kind, cap) per call."""
    calls = []

    async def run_specialist(kind, req, cities, cap):
        calls.append((kind, cap))
        return SimpleNamespace(total=costs[kind].pop(0))

    async def write_itinerary(req, cities, results, over_budget):
        return "# plan"

    monkeypatch.setattr(orchestrator, "run_specialist", run_specialist)
    monkeypatch.setattr(orchestrator, "write_itinerary", write_itinerary)
    monkeypatch.setattr(orchestrator, "resolve_city", lambda name: SimpleNamespace(code="X", name=name, lat=0.0, lon=0.0))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test_orchestrator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator'`

- [ ] **Step 3: Write `orchestrator.py`**

```python
"""Deterministic coordinator: resolve cities, split budget, run specialists in parallel, validate, retry, write."""
import asyncio
from dataclasses import dataclass
from datetime import date

from agents import Runner

from specialists import City, activity_agent, flight_agent, resolve_city, stay_agent, writer_agent

SPLIT = {"flight": 0.40, "stay": 0.35, "activity": 0.25}
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


def _brief(req: PlanRequest, cities: dict[str, City], cap: float) -> str:
    o, d = cities["origin"], cities["destination"]
    return (
        f"Trip: {o.name} ({o.code}) -> {d.name} ({d.code}); destination centre lat {d.lat}, lon {d.lon}.\n"
        f"Dates: depart {req.depart}, return {req.return_date} ({(req.return_date - req.depart).days} nights). Travelers: {req.travelers}.\n"
        f"Currency: {req.currency}. Your budget cap: {cap:.0f} {req.currency}.\n"
        f"Traveler preferences: {req.preferences.strip() or 'none given'}."
    )


async def run_specialist(kind: str, req: PlanRequest, cities: dict[str, City], cap: float):
    return (await Runner.run(AGENTS[kind], _brief(req, cities, cap))).final_output


async def write_itinerary(req: PlanRequest, cities: dict[str, City], results: dict, over_budget: bool) -> str:
    prompt = (
        _brief(req, cities, req.budget).replace("Your budget cap", "Total budget")
        + f"\nOver budget: {'yes' if over_budget else 'no'}.\n\nSpecialist picks (JSON):\n"
        + "\n".join(f"{k}: {r.model_dump_json()}" for k, r in results.items())
    )
    return (await Runner.run(writer_agent, prompt)).final_output


async def plan(req: PlanRequest, on_event=None) -> PlanResult:
    on_event = on_event or (lambda kind, status, detail: None)
    cities = {"origin": resolve_city(req.origin), "destination": resolve_city(req.destination)}
    caps = {k: req.budget * share for k, share in SPLIT.items()}

    async def run(kind):
        on_event(kind, "start", f"cap {caps[kind]:.0f} {req.currency}")
        r = await run_specialist(kind, req, cities, caps[kind])
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
        results[worst] = await run(worst)

    totals = {k: r.total for k, r in results.items()}
    over_budget = sum(totals.values()) > req.budget
    on_event("writer", "start", "")
    md = await write_itinerary(req, cities, results, over_budget)
    on_event("writer", "done", "")
    return PlanResult(md, totals, caps, over_budget)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py test_orchestrator.py
git commit -m "Add orchestrator with parallel agents and budget retry loop

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: SQLite plan history

**Files:**
- Create: `db.py`
- Test: `test_db.py`

**Interfaces:**
- Produces: `save(title: str, request: dict, itinerary: str, total: float, currency: str) -> int`; `list_plans() -> list[tuple[int, str, str, float, str]]` as `(id, created_at, title, total, currency)` newest first; `load(plan_id: int) -> str` itinerary markdown; module constant `DB: Path` (monkeypatch target).

- [ ] **Step 1: Write the failing test**

`test_db.py`:
```python
from datetime import date

import db


def test_save_list_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    pid = db.save("Madrid -> Paris", {"depart": date(2026, 10, 10)}, "# md", 950.0, "USD")
    assert db.list_plans()[0][0] == pid
    assert db.list_plans()[0][2:] == ("Madrid -> Paris", 950.0, "USD")
    assert db.load(pid) == "# md"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Write `db.py`**

```python
"""Plan history in a local SQLite file. Ephemeral inside the Hugging Face Spaces container (resets on rebuild) - fine for a demo."""
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("plans.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "title TEXT, request TEXT, itinerary TEXT, total REAL, currency TEXT)"
    )
    return c


def save(title: str, request: dict, itinerary: str, total: float, currency: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO plans (title, request, itinerary, total, currency) VALUES (?, ?, ?, ?, ?)",
            (title, json.dumps(request, default=str), itinerary, total, currency),
        )
        return cur.lastrowid


def list_plans() -> list[tuple]:
    with _conn() as c:
        return c.execute("SELECT id, created_at, title, total, currency FROM plans ORDER BY id DESC").fetchall()


def load(plan_id: int) -> str:
    with _conn() as c:
        return c.execute("SELECT itinerary FROM plans WHERE id = ?", (plan_id,)).fetchone()[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add db.py test_db.py
git commit -m "Add SQLite plan history

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: FastAPI streaming API

**Files:**
- Create: `api.py`
- Test: `test_api.py`

**Interfaces:**
- Consumes: `orchestrator.PlanRequest`, `orchestrator.plan`, `orchestrator.PlanResult`, `db.save/list_plans/load`.
- Produces: `GET /api/plan` (query params `origin, destination, depart, return_date, travelers, budget, currency, preferences`) → `text/event-stream`, each line `data: <json>\n\n`. Progress events are `{"kind": "flight"|"stay"|"activity"|"writer", "status": "start"|"done"|"retry", "detail": str}`; terminal event is `{"kind": "plan", "status": "done", "itinerary": str, "totals": {...}, "over_budget": bool, "id": int}` or `{"kind": "plan", "status": "error", "detail": str}`. `GET /api/plans` → `[{id, created_at, title, total, currency}]`. `GET /api/plans/{id}` → `{"itinerary": str}`. Static frontend mounted at `/` from `web/dist` when that folder exists.

- [ ] **Step 1: Write the failing tests**

`test_api.py`:
```python
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


def test_plan_streams_events_then_result_and_saves(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(api, "plan", fake_plan([("flight", "start", "cap 400 USD"), ("flight", "done", "380 USD")]))
    monkeypatch.setenv("OPENAI_API_KEY", "x"); monkeypatch.setenv("AMADEUS_CLIENT_ID", "x"); monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "x")
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
    monkeypatch.setenv("OPENAI_API_KEY", "x"); monkeypatch.setenv("AMADEUS_CLIENT_ID", "x"); monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "x")
    r = TestClient(api.app).get("/api/plan", params=PARAMS)
    assert sse_events(r.text) == [{"kind": "plan", "status": "error", "detail": "Amadeus does not know a city called 'Atlantis'"}]


def test_plan_rejects_bad_input(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x"); monkeypatch.setenv("AMADEUS_CLIENT_ID", "x"); monkeypatch.setenv("AMADEUS_CLIENT_SECRET", "x")
    client = TestClient(api.app)
    assert client.get("/api/plan", params={**PARAMS, "return_date": "2026-10-09"}).status_code == 422
    assert client.get("/api/plan", params={**PARAMS, "currency": "EUR"}).status_code == 422
    assert client.get("/api/plan", params={**PARAMS, "travelers": 0}).status_code == 422


def test_plan_reports_missing_keys(monkeypatch):
    monkeypatch.delenv("AMADEUS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x"); monkeypatch.setenv("AMADEUS_CLIENT_ID", "x")
    r = TestClient(api.app).get("/api/plan", params=PARAMS)
    assert r.status_code == 503 and "AMADEUS_CLIENT_SECRET" in r.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'api'`

- [ ] **Step 3: Write `api.py`**

```python
"""FastAPI front door: streams orchestrator progress over SSE and serves the built React app."""
import asyncio
import json
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
from orchestrator import PlanRequest, plan

load_dotenv()
app = FastAPI(title="Vacaya")

REQUIRED_KEYS = ("OPENAI_API_KEY", "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET")


@app.get("/api/plans")
def list_plans():
    return [dict(zip(("id", "created_at", "title", "total", "currency"), row)) for row in db.list_plans()]


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    return {"itinerary": db.load(plan_id)}


@app.get("/api/plan")
async def stream_plan(
    origin: str = Query(min_length=2),
    destination: str = Query(min_length=2),
    depart: date = Query(),
    return_date: date = Query(),
    travelers: int = Query(1, ge=1, le=9),
    budget: float = Query(gt=0),
    currency: Literal["USD", "INR"] = "USD",
    preferences: str = "",
):
    if return_date <= depart:
        raise HTTPException(422, "return_date must be after depart")
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise HTTPException(503, f"Missing secrets: {', '.join(missing)}")

    req = PlanRequest(origin, destination, depart, return_date, travelers, budget, currency, preferences)
    queue: asyncio.Queue = asyncio.Queue()

    async def work():
        try:
            result = await plan(req, lambda kind, status, detail: queue.put_nowait({"kind": kind, "status": status, "detail": detail}))
            total = sum(result.totals.values())
            pid = db.save(f"{origin} → {destination} {depart}", asdict(req), result.itinerary_md, total, currency)
            queue.put_nowait({"kind": "plan", "status": "done", "id": pid, "itinerary": result.itinerary_md,
                              "totals": result.totals, "over_budget": result.over_budget})
        except Exception as e:  # surfaced to the client as the terminal event
            queue.put_nowait({"kind": "plan", "status": "error", "detail": str(e)})

    async def events():
        task = asyncio.create_task(work())
        while True:
            ev = await queue.get()
            yield f"data: {json.dumps(ev)}\n\n"
            if ev["kind"] == "plan":
                break
        await task

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


dist = Path(__file__).with_name("web") / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add api.py test_api.py
git commit -m "Add FastAPI SSE endpoint and plan history routes

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: React frontend with 21st.dev components

**Files:**
- Create: `web/` (Vite React-TS scaffold), `web/src/App.tsx`, `web/src/lib/api.ts`, `web/src/components/TripForm.tsx`, `web/src/components/AgentProgress.tsx`, `web/src/components/ui/*` (from 21st.dev + shadcn), `web/vite.config.ts` (proxy), `web/src/index.css`

**Interfaces:**
- Consumes: the SSE contract and `/api/plans*` routes from Task 4.
- Produces: `web/dist` after `npm run build`, which `api.py` serves.

- [ ] **Step 1: Scaffold Vite + Tailwind + shadcn**

```bash
cd C:/Users/Dell/Desktop/vacaya
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install tailwindcss @tailwindcss/vite react-markdown remark-gfm @tailwindcss/typography
npx shadcn@latest init -y -d
```

`web/vite.config.ts`:
```ts
import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
```

Ensure `web/src/index.css` starts with:
```css
@import "tailwindcss";
@plugin "@tailwindcss/typography";
```
(followed by whatever `shadcn init` generated). Ensure `web/tsconfig.json` and `web/tsconfig.app.json` both have `"baseUrl": "."` and `"paths": {"@/*": ["./src/*"]}` under `compilerOptions` (shadcn init usually adds these; verify).

- [ ] **Step 2: Fetch the two 21st.dev components through the connector**

Call the 21st.dev MCP `get_component` with `id: 7508` (Booking Form by lavikatiyar) and `id: 23793` (AI Task List by educalvolpz). Each result contains the component source, its demo, and `registryDependencies`. For each:
1. Write the component source to `web/src/components/ui/<name>.tsx`.
2. Install its registry deps: `npx shadcn@latest add <dep> <dep>` (e.g. `button input label calendar popover`) and its npm deps (e.g. `npm install framer-motion lucide-react date-fns`).

If a result comes back `locked` (paywall) or `found: false`, substitute: for the form, `npx shadcn@latest add button input label select textarea card` and hand-write `TripForm.tsx` with those primitives; for the progress list, hand-write `AgentProgress.tsx` with `card` + `lucide-react` icons. Note the substitution in the commit message.

- [ ] **Step 3: Write `web/src/lib/api.ts`**

```ts
export type TripInput = {
  origin: string; destination: string; depart: string; return_date: string;
  travelers: number; budget: number; currency: "USD" | "INR"; preferences: string;
};
export type AgentKind = "flight" | "stay" | "activity" | "writer";
export type ProgressEvent = { kind: AgentKind; status: "start" | "done" | "retry"; detail: string };
export type PlanDone = { kind: "plan"; status: "done"; id: number; itinerary: string; totals: Record<string, number>; over_budget: boolean };
export type PlanError = { kind: "plan"; status: "error"; detail: string };
export type SseEvent = ProgressEvent | PlanDone | PlanError;
export type SavedPlan = { id: number; created_at: string; title: string; total: number; currency: string };

export function streamPlan(input: TripInput, onEvent: (e: SseEvent) => void, onFail: (msg: string) => void) {
  const qs = new URLSearchParams(Object.entries(input).map(([k, v]) => [k, String(v)]));
  const es = new EventSource(`/api/plan?${qs}`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data) as SseEvent;
    onEvent(ev);
    if (ev.kind === "plan") es.close();
  };
  es.onerror = async () => {
    es.close();
    // EventSource hides HTTP status; re-fetch to read the 4xx/5xx body for the message
    const r = await fetch(`/api/plan?${qs}`, { headers: { Accept: "application/json" } }).catch(() => null);
    onFail(r && !r.ok ? ((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`) : "Connection lost");
  };
  return () => es.close();
}

export const listPlans = () => fetch("/api/plans").then((r) => r.json() as Promise<SavedPlan[]>);
export const loadPlan = (id: number) => fetch(`/api/plans/${id}`).then((r) => r.json() as Promise<{ itinerary: string }>);
```

- [ ] **Step 4: Write `web/src/components/TripForm.tsx`**

Adapt the 21st.dev Booking Form's markup and animation to these fields, keeping its visual style. Props contract:

```tsx
import { useState } from "react";
import type { TripInput } from "@/lib/api";

const plusDays = (n: number) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);

export function TripForm({ onSubmit, disabled }: { onSubmit: (t: TripInput) => void; disabled: boolean }) {
  const [t, set] = useState<TripInput>({
    origin: "Madrid", destination: "Paris", depart: plusDays(30), return_date: plusDays(34),
    travelers: 1, budget: 2000, currency: "USD", preferences: "",
  });
  const upd = <K extends keyof TripInput>(k: K, v: TripInput[K]) => set((s) => ({ ...s, [k]: v }));
  const invalid = t.return_date <= t.depart || t.budget <= 0 || !t.origin || !t.destination;

  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!invalid) onSubmit(t); }} className="...21st.dev form classes...">
      {/* origin / destination text inputs */}
      {/* depart / return <input type="date"> */}
      {/* travelers <input type="number" min=1 max=9> */}
      {/* budget <input type="number" min=1 step=50> + currency <select> USD/INR */}
      {/* preferences <textarea> placeholder "non-stop flights, boutique hotel near the centre, museums, vegetarian food" */}
      <button type="submit" disabled={disabled || invalid}>Plan my trip</button>
    </form>
  );
}
```

Use native `<input type="date">`, `<input type="number">` and `<select>` styled with the component's classes; do not add a date-picker library.

- [ ] **Step 5: Write `web/src/components/AgentProgress.tsx`**

Feed the 21st.dev AI Task List with four tasks derived from the event log. Mapping:

```tsx
import type { ProgressEvent, AgentKind } from "@/lib/api";

const LABEL: Record<AgentKind, string> = { flight: "Flight agent", stay: "Stay agent", activity: "Activity agent", writer: "Itinerary writer" };
export type TaskState = "pending" | "running" | "done" | "failed";

export function toTasks(events: ProgressEvent[], failed: boolean) {
  return (Object.keys(LABEL) as AgentKind[]).map((kind) => {
    const mine = events.filter((e) => e.kind === kind);
    const last = mine.at(-1);
    const state: TaskState = failed && last?.status !== "done" ? "failed" : !last ? "pending" : last.status === "done" ? "done" : "running";
    const subtasks = mine.filter((e) => e.status === "retry").map((e, i) => ({ title: `Retry ${i + 1}: ${e.detail}`, state: "done" as TaskState }));
    const detail = last?.status === "done" ? last.detail : last?.status === "start" ? last.detail : "";
    return { id: kind, title: LABEL[kind], detail, state, subtasks };
  });
}

export function AgentProgress({ events, failed }: { events: ProgressEvent[]; failed: boolean }) {
  const tasks = toTasks(events, failed);
  return /* render the 21st.dev AI Task List with `tasks` — map its expected prop shape onto {title, detail, state, subtasks} */;
}
```

- [ ] **Step 6: Write `web/src/App.tsx`**

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { TripForm } from "@/components/TripForm";
import { AgentProgress } from "@/components/AgentProgress";
import { listPlans, loadPlan, streamPlan, type PlanDone, type ProgressEvent, type SavedPlan, type TripInput } from "@/lib/api";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<PlanDone | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SavedPlan[]>([]);
  const [currency, setCurrency] = useState("USD");

  useEffect(() => { listPlans().then(setSaved); }, [phase]);

  function start(t: TripInput) {
    setPhase("running"); setEvents([]); setResult(null); setError(""); setCurrency(t.currency);
    streamPlan(t, (ev) => {
      if (ev.kind !== "plan") return setEvents((es) => [...es, ev]);
      if (ev.status === "done") { setResult(ev); setPhase("done"); }
      else { setError(ev.detail); setPhase("error"); }
    }, (msg) => { setError(msg); setPhase("error"); });
  }

  async function open(p: SavedPlan) {
    const { itinerary } = await loadPlan(p.id);
    setResult({ kind: "plan", status: "done", id: p.id, itinerary, totals: {}, over_budget: false });
    setCurrency(p.currency); setEvents([]); setPhase("done");
  }

  const total = result ? Object.values(result.totals).reduce((a, b) => a + b, 0) : 0;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid max-w-6xl gap-6 p-4 md:grid-cols-[260px_1fr] md:p-8">
        <aside>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Past plans</h2>
          <ul className="space-y-1">
            {saved.map((p) => (
              <li key={p.id}>
                <button onClick={() => open(p)} className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted">
                  {p.title} · {p.total.toLocaleString()} {p.currency}
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <main className="space-y-6">
          <header>
            <h1 className="text-3xl font-bold">Vacaya</h1>
            <p className="text-muted-foreground">Three specialist agents, one budget, one itinerary.</p>
          </header>
          <TripForm onSubmit={start} disabled={phase === "running"} />
          {(phase === "running" || events.length > 0) && <AgentProgress events={events} failed={phase === "error"} />}
          {phase === "error" && <p role="alert" className="rounded-md border border-destructive p-3 text-destructive">{error}</p>}
          {result && (
            <section className="space-y-3">
              {result.totals.flight !== undefined && (
                <p className={result.over_budget ? "text-destructive" : "text-green-600"}>
                  {result.over_budget ? "Over budget after retries" : "Within budget"}: {total.toLocaleString()} {currency}
                </p>
              )}
              <article className="prose prose-neutral max-w-none dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.itinerary}</ReactMarkdown>
              </article>
              <a className="inline-block text-sm underline" download="itinerary.md"
                 href={`data:text/markdown;charset=utf-8,${encodeURIComponent(result.itinerary)}`}>Download itinerary (.md)</a>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Type-check and build**

Run: `cd web && npx tsc --noEmit -p tsconfig.app.json && npm run build`
Expected: no type errors; `web/dist/index.html` exists.

- [ ] **Step 8: Live smoke test (needs `.env` with real keys)**

Terminal 1: `.venv/Scripts/python -m uvicorn api:app --reload --port 8000`
Terminal 2: `cd web && npm run dev`
Open http://localhost:5173, submit Madrid → Paris. Expected: four tasks go pending → running → done in the task list (a retry shows as a subtask if the first pass overshoots), itinerary renders with a cost table, the plan appears in the sidebar, download link works. Then stop the dev server and confirm http://localhost:8000 serves the built app from `web/dist`.

- [ ] **Step 9: Commit**

```bash
git add web
git commit -m "Add React frontend with 21st.dev booking form and agent task list

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Dockerfile, README, Hugging Face Spaces deploy

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Create: `README.md`

- [ ] **Step 1: Write `Dockerfile` and `.dockerignore`**

`Dockerfile`:
```dockerfile
FROM node:24-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web .
RUN npm run build

FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY --from=web /web/dist ./web/dist
EXPOSE 7860
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]
```

`.dockerignore`:
```
.venv
.env
plans.db
web/node_modules
web/dist
docs
test_*.py
__pycache__
.git
```

- [ ] **Step 2: Build and run the container locally**

Run: `docker build -t vacaya . && docker run --rm -p 7860:7860 --env-file .env vacaya`
Open http://localhost:7860 and run one plan end to end. Expected: same behaviour as the dev setup.

- [ ] **Step 3: Write `README.md`** (the YAML front-matter is required by Hugging Face Spaces)

````markdown
---
title: Vacaya
emoji: 🧳
sdk: docker
app_port: 7860
---

# Vacaya - multi-agent travel planner

Enter origin, destination, dates, budget (USD/INR) and preferences. Three specialist agents search Amadeus in parallel
(flights, hotels, activities), a deterministic orchestrator checks the total against the budget and re-runs whichever
agent overshot with a tighter cap, then a writer agent produces a day-by-day itinerary. Plans are saved to SQLite.

```
React UI (21st.dev components) --EventSource--> FastAPI /api/plan (SSE) --> orchestrator.plan()
                                                                              |-- resolve cities (Amadeus Locations)
                                                                              |-- split budget 40/35/25
                                                                              |-- asyncio.gather: flight | stay | activity agents (OpenAI Agents SDK + Amadeus tools)
                                                                              |-- validate total; retry worst offender with reduced cap (max 2)
                                                                              '-- writer agent -> Markdown itinerary -> SQLite
```

## Run locally

1. `python -m venv .venv && .venv/Scripts/activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `OPENAI_API_KEY` from https://platform.openai.com
   - `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` from https://developers.amadeus.com (Self-Service, free test environment)
4. Backend: `uvicorn api:app --reload --port 8000`
5. Frontend: `cd web && npm install && npm run dev` then open http://localhost:5173

Or build once and serve everything from FastAPI: `cd web && npm run build`, then http://localhost:8000.

## Tests

`python -m pytest` - no network; agents, Amadeus and the orchestrator are faked.

## Deploy (Hugging Face Spaces, Docker)

1. Create a Space at https://huggingface.co/new-space with SDK **Docker**.
2. Space settings -> Variables and secrets: add `OPENAI_API_KEY`, `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` as secrets.
3. `git remote add hf https://huggingface.co/spaces/<user>/vacaya && git push hf master:main`

Plan history lives in a SQLite file inside the container, which resets on rebuild.

## Amadeus test environment caveats

The free test environment only has data for major cities and hotel offers are often empty. Known-good demo inputs:
Madrid -> Paris, London -> Barcelona, New York -> San Francisco. If a section reports "no options found", that is the
test data, not a bug - the agents never invent prices.
````

- [ ] **Step 4: Commit and push to GitHub**

```bash
git add Dockerfile .dockerignore README.md
git commit -m "Add Dockerfile and README for Hugging Face Spaces

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

- [ ] **Step 5: Deploy to Hugging Face Spaces (user creates the Space and secrets; then push)**

```bash
git remote add hf https://huggingface.co/spaces/<hf-user>/vacaya
git push hf master:main
```

Confirm the Space builds (Logs tab), loads, and one plan completes end to end.
