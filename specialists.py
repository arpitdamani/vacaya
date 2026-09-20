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
