# Vacaya Multi-Agent Travel Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Streamlit app where a user enters origin, destination, dates, travelers, budget (USD/INR) and preferences, and three parallel specialist agents (flights, stay, activities) backed by Amadeus produce typed picks that a deterministic orchestrator validates against the budget (retrying over-budget agents) before a writer agent renders a day-by-day itinerary, saved to SQLite.

**Architecture:** `orchestrator.plan()` is plain async Python: resolve cities via Amadeus, split budget 40/35/25, `asyncio.gather` three OpenAI Agents SDK agents (each with one Amadeus `function_tool` and a Pydantic `output_type`), sum `.total`, re-run the worst offender with a reduced cap up to 2 times, then call a tool-less writer agent for Markdown. `app.py` drives it with `st.status` boxes fed by an `on_event` callback and persists results through `db.py`.

**Tech Stack:** Python 3.14, openai-agents 0.22, amadeus 12, streamlit 1.64, python-dotenv, pytest, sqlite3 (stdlib), urllib (stdlib) for frankfurter.app FX.

## Global Constraints

- Module for agents is `specialists.py`, never `agents.py` (collides with the SDK package).
- Every specialist output model has a `total: float` field — the orchestrator's budget math depends on it.
- Tools return JSON strings; on Amadeus error they return `{"error": "..."}`; never fabricated data.
- Model is `os.getenv("OPENAI_MODEL")` → `None` means SDK default. Same for all agents.
- Currency is `USD` or `INR`; flights/hotels request it via Amadeus; activity prices converted in-tool via frankfurter.app, falling back to source currency when the rate fetch fails.
- Budget split constants: flight 0.40, stay 0.35, activity 0.25. `MAX_RETRIES = 2`.
- Tests never hit OpenAI or Amadeus.
- Commit after every task with the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer.
- Venv already exists at `.venv`; run Python as `.venv/Scripts/python` and tests as `.venv/Scripts/python -m pytest`.

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
streamlit
python-dotenv
pytest
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
.streamlit/secrets.toml
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
"""Plan history in a local SQLite file. Ephemeral on Streamlit Cloud (resets on redeploy) - fine for a demo."""
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

### Task 4: Streamlit app + README + live smoke test

**Files:**
- Create: `app.py`, `README.md`

**Interfaces:**
- Consumes: `orchestrator.PlanRequest`, `orchestrator.plan`, `db.save/list_plans/load`.

- [ ] **Step 1: Write `app.py`**

```python
import asyncio
import os
from dataclasses import asdict
from datetime import date, timedelta

import streamlit as st
from dotenv import load_dotenv

import db
from orchestrator import PlanRequest, plan

load_dotenv()  # local dev; on Streamlit Cloud, dashboard secrets are exported as env vars automatically

st.set_page_config(page_title="Vacaya", page_icon="🧳", layout="wide")
st.title("Vacaya - multi-agent travel planner")

missing = [k for k in ("OPENAI_API_KEY", "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET") if not os.getenv(k)]
if missing:
    st.error(f"Missing secrets: {', '.join(missing)}. Put them in .env locally or in Streamlit Cloud secrets.")
    st.stop()

LABELS = {"flight": "✈️ Flight agent", "stay": "🏨 Stay agent", "activity": "🎟️ Activity agent", "writer": "📝 Itinerary writer"}

with st.sidebar:
    st.header("Past plans")
    for pid, created, title, total, cur in db.list_plans():
        if st.button(f"{title} · {total:,.0f} {cur}", key=f"plan{pid}", width="stretch"):
            st.session_state["md"] = db.load(pid)

with st.form("trip"):
    c1, c2 = st.columns(2)
    origin = c1.text_input("From", "Madrid")
    destination = c2.text_input("To", "Paris")
    depart = c1.date_input("Depart", date.today() + timedelta(days=30))
    ret = c2.date_input("Return", date.today() + timedelta(days=34))
    travelers = c1.number_input("Travelers", 1, 9, 1)
    currency = c2.selectbox("Currency", ["USD", "INR"])
    budget = st.number_input("Total budget", min_value=100.0, value=2000.0, step=100.0)
    prefs = st.text_area("Preferences", placeholder="non-stop flights, boutique hotel near the centre, museums, vegetarian food, no nightlife")
    go = st.form_submit_button("Plan my trip", type="primary")

if go:
    if ret <= depart:
        st.error("Return date must be after departure.")
        st.stop()
    req = PlanRequest(origin, destination, depart, ret, int(travelers), float(budget), currency, prefs)
    boxes = {k: st.status(label, state="running") for k, label in LABELS.items()}

    def on_event(kind, status, detail):
        box = boxes[kind]
        if status == "start":
            box.update(label=f"{LABELS[kind]} - searching ({detail})" if detail else f"{LABELS[kind]} - writing", state="running")
        elif status == "retry":
            box.write(f"Budget check failed: {detail}. Re-running.")
            box.update(label=f"{LABELS[kind]} - retrying", state="running", expanded=True)
        elif status == "done":
            box.update(label=f"{LABELS[kind]} - done {detail}", state="complete")

    try:
        result = asyncio.run(plan(req, on_event))
    except Exception as e:  # surfaced to the user; nothing to recover
        st.error(f"Planning failed: {e}")
        st.stop()

    st.session_state["md"] = result.itinerary_md
    db.save(f"{origin} → {destination} {depart}", asdict(req), result.itinerary_md, sum(result.totals.values()), currency)
    if result.over_budget:
        st.warning(f"Still over budget after retries: {sum(result.totals.values()):,.0f} {currency} vs {budget:,.0f} {currency}.")
    else:
        st.success(f"Within budget: {sum(result.totals.values()):,.0f} {currency} of {budget:,.0f} {currency}.")

if "md" in st.session_state:
    st.markdown(st.session_state["md"])
    st.download_button("Download itinerary (.md)", st.session_state["md"], "itinerary.md")
```

- [ ] **Step 2: Write `README.md`**

````markdown
# Vacaya - multi-agent travel planner

Enter origin, destination, dates, budget (USD/INR) and preferences. Three specialist agents search Amadeus in parallel
(flights, hotels, activities), a deterministic orchestrator checks the total against the budget and re-runs whichever
agent overshot with a tighter cap, then a writer agent produces a day-by-day itinerary. Plans are saved to SQLite.

```
user request -> orchestrator.plan()
                  |-- resolve cities (Amadeus Locations)
                  |-- split budget 40/35/25
                  |-- asyncio.gather: flight agent | stay agent | activity agent   (OpenAI Agents SDK + Amadeus tools)
                  |-- validate total; retry worst offender with reduced cap (max 2)
                  '-- writer agent -> Markdown itinerary -> SQLite -> Streamlit
```

## Run locally

1. `python -m venv .venv && .venv/Scripts/activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `OPENAI_API_KEY` from https://platform.openai.com
   - `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` from https://developers.amadeus.com (Self-Service, free test environment)
4. `streamlit run app.py`

## Tests

`python -m pytest` - no network; agents and Amadeus are faked.

## Deploy (Streamlit Community Cloud)

1. Push to GitHub (public repo).
2. https://share.streamlit.io -> New app -> pick the repo, branch `master`, file `app.py`.
3. Advanced settings -> Secrets: paste the three keys in TOML form (`OPENAI_API_KEY = "..."` etc.).

Plan history lives in a SQLite file on the app's disk, which resets on redeploy.

## Amadeus test environment caveats

The free test environment only has data for major cities and hotel offers are often empty. Known-good demo inputs:
Madrid -> Paris, London -> Barcelona, New York -> San Francisco. If a section reports "no options found", that is the
test data, not a bug - the agents never invent prices.
````

- [ ] **Step 3: Create `.env` from `.env.example` with real keys** (user supplies keys; never commit `.env`)

- [ ] **Step 4: Live smoke test**

Run: `.venv/Scripts/python -m streamlit run app.py --server.headless true`
Open http://localhost:8501, submit the default Madrid -> Paris form. Expected: four status boxes go running -> complete, an itinerary renders with a cost table, the plan appears in the sidebar after a rerun, download button works. Check the terminal for tracebacks.

If Amadeus returns an error for the default cities, try London -> Barcelona.

- [ ] **Step 5: Commit and push**

```bash
git add app.py README.md
git commit -m "Add Streamlit UI and README

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
```

---

### Task 5: Deploy to Streamlit Community Cloud (user-driven)

No code. The user signs in at https://share.streamlit.io with the GitHub account `arpitdamani`, creates the app from `arpitdamani/vacaya` (`master`, `app.py`), and pastes secrets:

```toml
OPENAI_API_KEY = "sk-..."
AMADEUS_CLIENT_ID = "..."
AMADEUS_CLIENT_SECRET = "..."
```

- [ ] Confirm the deployed URL loads and one plan completes end to end.
