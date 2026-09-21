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
from typing import Literal

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
            "airline_logo": o.get("airline_logo") or segs[0].get("airline_logo") or "",
        })
    return json.dumps(out[:10])


def _search_hotels(city: str, check_in: str, check_out: str, adults: int, currency: str, max_nightly: int) -> str:
    """Search hotels on Google Hotels for the whole stay. Returns a JSON list (Google's ranking, filtered to the nightly cap) with name, nightly and total price, star class, rating, Google's description, top amenities, nearby places and a photo URL.

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
            "reviews": p.get("reviews"),
            "description": (p.get("description") or "")[:200],
            "amenities": (p.get("amenities") or [])[:6],
            "nearby": [n.get("name") for n in (p.get("nearby_places") or [])[:3]],
            "image": ((p.get("images") or [{}])[0]).get("thumbnail") or "",
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


def _search_sights(city: str, currency: str) -> str:
    """Google's "top sights" for a city: landmarks, museums, beaches, parks. Returns a JSON list with name, rating, reviews, listed price (converted to `currency` when possible) and a photo URL. Only sights with a listed price (including "Free") are returned.

    Args:
        city: destination city or region name, e.g. Paris or Goa
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
            "reviews": s.get("reviews"),
            "price": amount,
            "currency": cur,
            "image": s.get("thumbnail") or "",
        })
    return json.dumps(out[:40])


def _search_places(query: str, city: str) -> str:
    """Google Maps search for experiences, tours, water sports, classes, restaurants, cafes, bars and nightclubs. Returns a JSON list with name, category, rating, review count, address, opening status and a photo URL. Google Maps does not list prices, so estimate typical per-person prices yourself and flag them as estimates.

    Args:
        query: what to look for, specific and preference-driven, e.g. "parasailing and jet ski", "vegetarian restaurants", "nightclubs", "cooking class", "rooftop bars"
        city: destination city or region name, e.g. Goa
    """
    data = serpapi(engine="google_maps", q=f"{query} in {city}", type="search", hl="en")
    if "error" in data:
        return json.dumps({"error": data["error"]})
    out = []
    for r in data.get("local_results") or []:
        if not r.get("title"):
            continue
        out.append({
            "name": r["title"],
            "category": r.get("type"),
            "rating": r.get("rating"),
            "reviews": r.get("reviews"),
            "address": r.get("address"),
            "open": r.get("open_state"),
            "image": r.get("thumbnail") or "",
        })
    return json.dumps(out[:12])


search_flights = function_tool(_search_flights, name_override="search_flights")
search_hotels = function_tool(_search_hotels, name_override="search_hotels")
search_sights = function_tool(_search_sights, name_override="search_sights")
search_places = function_tool(_search_places, name_override="search_places")


# ---------- typed outputs ----------

class FlightPick(BaseModel):
    airline: str
    depart_at: str
    return_at: str
    stops: int
    total: float
    image: str  # airline logo URL or ""
    reason: str


class StayPick(BaseModel):
    name: str
    description: str  # 2-3 sentences: what the place is like, the area, standout amenities
    stars: str
    rating: float
    nightly: float
    total: float
    image: str  # photo URL or ""
    reason: str


class ActivityItem(BaseModel):
    name: str
    kind: Literal["sight", "experience", "food", "nightlife"]
    description: str  # 1-2 concrete sentences: what the traveler will do or see and why it fits their preferences
    price: float  # total for all travelers in the plan currency; 0 if free
    estimated: bool  # True when no listed price existed and this is a typical-price estimate
    rating: float  # 0 if unknown
    image: str  # photo URL or ""
    day: int  # 1-based day of the trip
    time_of_day: Literal["morning", "afternoon", "evening"]


class ActivityPlan(BaseModel):
    items: list[ActivityItem]
    total: float
    estimated_total: float  # the part of `total` that is estimated rather than listed
    currency: str
    reason: str


# ---------- agents ----------

_COMMON = """You are one specialist in a team planning a trip. You receive the trip details, the traveler's preferences and a budget cap for your part.
Call your search tool once, then pick the single best option that fits the preferences and stays within the cap. Prefer the cheapest option that still matches the preferences.
If the tool returns an error or no options, do not invent anything: set total to 0, leave other fields empty or 0, and explain in `reason`. If every option exceeds the cap, pick the cheapest and say so in `reason`.
Write `reason` as one or two sentences addressed to the traveler explaining why this pick matches their preferences. """

flight_agent = Agent(
    name="Flight agent",
    instructions=_COMMON + "You handle round-trip flights. Convert the city names to the main IATA airport codes yourself (e.g. Madrid -> MAD, Paris -> CDG, London -> LHR, Mumbai -> BOM, Goa -> GOI, New York -> JFK). `airline` is the carrier name(s); `depart_at` is the outbound departure time; `return_at` is the return date (Google pairs the cheapest return leg); `image` is the airline_logo from the tool result.",
    tools=[search_flights],
    output_type=FlightPick,
    model=MODEL,
)

stay_agent = Agent(
    name="Stay agent",
    instructions=_COMMON + "You handle the hotel for the whole stay. Set `max_nightly` to your cap divided by the number of nights. Prefer well-rated places (rating >= 4) that match the preferences over the absolute cheapest. `total` is the price for all nights. Write `description` in 2-3 sentences from the tool's description, amenities and nearby places: what the place is like, where it is, what stands out. Copy `stars`, `rating` and `image` from the tool result.",
    tools=[search_hotels],
    output_type=StayPick,
    model=MODEL,
)

activity_agent = Agent(
    name="Experience agent",
    instructions="""You are one specialist in a team planning a trip: you plan what the traveler does, eats and where they go out, day by day. You receive the trip details, the traveler's preferences and a budget cap for everything you pick.
Research first: call `search_sights` once, then call `search_places` 2 to 4 times with specific queries driven by the preferences and the destination - e.g. "parasailing and jet ski" or "kayaking tours" for beach destinations, "vegetarian restaurants" or "seafood restaurants" for food preferences, "nightclubs" or "beach clubs" or "rooftop bars" when nightlife is wanted, "cooking class", "museums", "walking tours" as fits. Do not repeat the same query.
Then build the plan: every day of the trip except the departure day must have 1-2 sights or experiences (morning/afternoon) and 1 dinner spot (evening) - never leave a day empty; add nightlife on 1-2 evenings if the preferences ask for it. Vary the days and neighbourhoods. `day` is 1-based; the departure day gets at most one light morning item.
Prices: use a listed price from `search_sights` when there is one (estimated=false). Google Maps has no prices, so for experiences, meals and nightlife give a typical total price for all travelers in the plan currency and set estimated=true - be realistic for the destination. Never present an estimate as a quote. Keep `total` (listed + estimated) within the cap; `estimated_total` is the estimated part.
`description`: 1-2 concrete sentences on what the traveler will actually do or see there and why it fits their preferences. `rating` and `image` come from the tool results (0 / "" if absent).
If a tool errors or returns nothing, say so in `reason` and do not invent places. `reason`: two sentences addressed to the traveler on how the mix matches their preferences and budget.""",
    tools=[search_sights, search_places],
    output_type=ActivityPlan,
    model=MODEL,
)

writer_agent = Agent(
    name="Itinerary writer",
    instructions="""You write the final travel itinerary in Markdown from the specialists' picks (given as JSON). Use every item you are given; do not add places that are not in the picks. Structure:
1. `# <catchy title>` then a 2-3 sentence overview in the traveler's terms.
2. `## Cost` table with rows Flights, Stay, Experiences & dining, Total, Budget, Difference, in the given currency. If part of the experiences total is estimated, add a one-line note under the table: "~X of the experiences figure is a typical-price estimate, not a quote."
3. `## Getting there` - the flight: airline logo as `![airline](image)` if an image is given, airline, outbound time, stops, return date, price.
4. `## Where you're staying` - the hotel photo as `![name](image)` if given, then name, stars, rating, nightly and total price, and the description.
5. One section per day: `## Day N - <weekday, date>`. Inside, `### Morning`, `### Afternoon`, `### Evening` as needed. Each item: the photo on its own line as `![name](image)` when an image is given, then `**Name** · <kind> · ★<rating> · <price or "~price est." or "free">` and the description on the next line. Put the outbound flight and hotel check-in on day 1 and check-out plus the return flight on the last day.
6. `## Why these picks` - quote each specialist's reason.
Rules: prices only from the picks; mark estimates with "~" and "est."; if a specialist reports no options, say so plainly. If the plan is over budget, say so in the first line with the amount.""",
    model=MODEL,
)
