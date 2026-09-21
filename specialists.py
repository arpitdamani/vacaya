"""SerpApi-backed tools (Google Flights / Hotels / top sights), typed outputs and the four agents.
Orchestration lives in orchestrator.py."""
import json
import os
import re
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from agents import Agent, function_tool
from pydantic import BaseModel

MODEL = os.getenv("OPENAI_MODEL")  # None -> SDK default


def serpapi(**params) -> dict:
    """One SerpApi search. Returns the JSON dict; on any failure returns {"error": "..."} so tools never raise."""
    params = {k: v for k, v in params.items() if v is not None} | {"api_key": os.environ["SERPAPI_API_KEY"]}
    try:
        with urlopen(f"https://serpapi.com/search.json?{urlencode(params)}", timeout=30) as r:
            return json.load(r)
    except HTTPError as e:
        try:
            return {"error": json.load(e).get("error", str(e))}
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


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
    """Search round-trip flights on Google Flights. Returns a JSON list of outbound options, each with the round-trip price for all travelers (Google pairs the cheapest return), airlines, times, stops and total duration in minutes.

    Args:
        origin: IATA airport code of the departure airport, e.g. MAD, JFK, BOM
        destination: IATA airport code of the arrival airport, e.g. CDG, LHR, DEL
        depart: outbound date YYYY-MM-DD
        return_date: return date YYYY-MM-DD
        adults: number of travelers
        currency: ISO currency code, USD or INR
    """
    data = serpapi(engine="google_flights", departure_id=origin.upper(), arrival_id=destination.upper(),
                   outbound_date=depart, return_date=return_date, adults=adults, currency=currency, type=1, hl="en")
    if "error" in data:
        return json.dumps({"error": data["error"]})
    out = []
    for o in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        segs = o.get("flights") or []
        if not segs or o.get("price") is None:
            continue
        out.append({
            "total": float(o["price"]),
            "currency": currency,
            "airlines": sorted({s.get("airline", "?") for s in segs}),
            "depart_at": segs[0]["departure_airport"].get("time"),
            "arrive_at": segs[-1]["arrival_airport"].get("time"),
            "stops": len(o.get("layovers") or []),
            "duration_min": o.get("total_duration"),
        })
    return json.dumps(out[:10])


def _search_hotels(city: str, check_in: str, check_out: str, adults: int, currency: str, max_nightly: int) -> str:
    """Search hotels on Google Hotels for the whole stay. Returns a JSON list (Google's ranking, filtered to the nightly cap) with name, nightly and total price, star class, rating and a short description.

    Args:
        city: destination city name, e.g. Paris
        check_in: check-in date YYYY-MM-DD
        check_out: check-out date YYYY-MM-DD
        adults: number of guests
        currency: ISO currency code, USD or INR
        max_nightly: maximum price per night in `currency` (whole number); derive it from your cap divided by the number of nights
    """
    data = serpapi(engine="google_hotels", q=f"{city} hotels", check_in_date=check_in, check_out_date=check_out,
                   adults=adults, currency=currency, max_price=max(int(max_nightly), 1), hl="en", gl="us")
    if "error" in data:
        return json.dumps({"error": data["error"]})
    out = []
    for p in data.get("properties") or []:
        total = (p.get("total_rate") or {}).get("extracted_lowest")
        if total is None:
            continue
        out.append({
            "name": p.get("name"),
            "total": float(total),
            "nightly": (p.get("rate_per_night") or {}).get("extracted_lowest"),
            "currency": currency,
            "stars": p.get("hotel_class"),
            "rating": p.get("overall_rating"),
            "description": (p.get("description") or "")[:160],
        })
    return json.dumps(out[:15])


_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}


def _parse_price(text: str | None) -> tuple[float, str] | None:
    """'$25' -> (25.0, 'USD'); 'Free' -> (0.0, 'USD'); None/unparseable -> None."""
    if not text:
        return None
    if text.strip().lower() == "free":
        return 0.0, "USD"
    m = re.search(r"[\d][\d,]*(?:\.\d+)?", text)
    if not m:
        return None
    cur = next((c for s, c in _SYMBOL.items() if s in text), "USD")
    return float(m.group().replace(",", "")), cur


def _search_activities(city: str, currency: str) -> str:
    """Find top sights and things to do in a city (Google 'top sights'). Returns a JSON list with name, rating, price and currency. Prices are converted to `currency` when an exchange rate is available; otherwise the item keeps its original currency. Items without a listed price are omitted.

    Args:
        city: destination city name, e.g. Paris
        currency: ISO currency code the traveler budgets in, USD or INR
    """
    data = serpapi(engine="google", q=f"top sights in {city}", hl="en", gl="us")
    if "error" in data:
        return json.dumps({"error": data["error"]})
    out = []
    for s in (data.get("top_sights") or {}).get("sights") or []:
        parsed = _parse_price(s.get("price"))
        if parsed is None:
            continue
        amount, cur = parsed
        rate = fx_rate(cur, currency)
        if rate is not None:
            amount, cur = round(amount * rate, 2), currency
        out.append({
            "name": s.get("title"),
            "rating": s.get("rating"),
            "price": amount,
            "currency": cur,
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
    instructions=_COMMON + "You handle round-trip flights. Convert the city names to the main IATA airport codes yourself (e.g. Madrid -> MAD, Paris -> CDG, London -> LHR, Mumbai -> BOM, New York -> JFK). `airline` is the carrier name(s); `depart_at` is the outbound departure time; `return_at` is the return date (Google pairs the cheapest return leg).",
    tools=[search_flights],
    output_type=FlightPick,
    model=MODEL,
)

stay_agent = Agent(
    name="Stay agent",
    instructions=_COMMON + "You handle the hotel for the whole stay. Set `max_nightly` to your cap divided by the number of nights. `total` is the price for all nights; `room` is a short description (star class, rating, what stands out).",
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
