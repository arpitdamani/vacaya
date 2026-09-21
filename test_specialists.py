import json

import specialists
from specialists import _parse_price, _search_activities, _search_flights, _search_hotels


def fake_serpapi(monkeypatch, response):
    calls = []
    monkeypatch.setattr(specialists, "serpapi", lambda **params: (calls.append(params), response)[1])
    return calls


def test_search_flights_reshapes_offer(monkeypatch):
    seg = lambda dep, arr, airline: {"departure_airport": {"time": dep}, "arrival_airport": {"time": arr}, "airline": airline}
    calls = fake_serpapi(monkeypatch, {
        "best_flights": [{"price": 123, "total_duration": 300, "layovers": [{"duration": 60}],
                          "flights": [seg("2026-10-10 08:00", "2026-10-10 10:00", "Iberia"), seg("2026-10-10 11:00", "2026-10-10 13:00", "Air France")]}],
        "other_flights": [{"price": 99, "total_duration": 130, "flights": [seg("2026-10-10 18:00", "2026-10-10 20:10", "Vueling")]}],
    })
    out = json.loads(_search_flights("mad", "CDG", "2026-10-10", "2026-10-14", 2, "USD"))
    assert calls[0]["departure_id"] == "MAD" and calls[0]["adults"] == 2 and calls[0]["type"] == 1
    assert out == [
        {"total": 123.0, "currency": "USD", "airlines": ["Air France", "Iberia"], "depart_at": "2026-10-10 08:00",
         "arrive_at": "2026-10-10 13:00", "stops": 1, "duration_min": 300},
        {"total": 99.0, "currency": "USD", "airlines": ["Vueling"], "depart_at": "2026-10-10 18:00",
         "arrive_at": "2026-10-10 20:10", "stops": 0, "duration_min": 130},
    ]


def test_search_flights_passes_api_error_through(monkeypatch):
    fake_serpapi(monkeypatch, {"error": "Google Flights hasn't returned any results for this query."})
    assert json.loads(_search_flights("MAD", "CDG", "2026-10-10", "2026-10-14", 1, "USD")) == {"error": "Google Flights hasn't returned any results for this query."}


def test_search_hotels_reshapes_and_skips_unpriced(monkeypatch):
    calls = fake_serpapi(monkeypatch, {"properties": [
        {"name": "Hotel A", "total_rate": {"extracted_lowest": 400}, "rate_per_night": {"extracted_lowest": 100},
         "hotel_class": "4-star hotel", "overall_rating": 4.4, "description": "Near the Louvre"},
        {"name": "Sold out", "total_rate": {}},
    ]})
    out = json.loads(_search_hotels("Paris", "2026-10-10", "2026-10-14", 1, "USD", 120))
    assert calls[0]["q"] == "Paris hotels" and calls[0]["max_price"] == 120 and calls[0]["currency"] == "USD"
    assert out == [{"name": "Hotel A", "total": 400.0, "nightly": 100, "currency": "USD", "stars": "4-star hotel",
                    "rating": 4.4, "description": "Near the Louvre"}]


def test_parse_price():
    assert _parse_price("$25") == (25.0, "USD")
    assert _parse_price("From €1,200.50") == (1200.5, "EUR")
    assert _parse_price("Free") == (0.0, "USD")
    assert _parse_price(None) is None
    assert _parse_price("Varies") is None


def test_search_activities_converts_currency_and_skips_unpriced(monkeypatch):
    fake_serpapi(monkeypatch, {"top_sights": {"sights": [
        {"title": "Louvre", "description": "Art", "rating": 4.7, "price": "$20"},
        {"title": "Seine walk"},
    ]}})
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: 2.0)
    out = json.loads(_search_activities("Paris", "INR"))
    assert out == [{"name": "Louvre", "rating": 4.7, "price": 40.0, "currency": "INR"}]


def test_search_activities_keeps_source_currency_when_fx_fails(monkeypatch):
    fake_serpapi(monkeypatch, {"top_sights": {"sights": [{"title": "X", "price": "€5"}]}})
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: None)
    out = json.loads(_search_activities("Paris", "INR"))
    assert out[0]["price"] == 5.0 and out[0]["currency"] == "EUR"


def test_tools_have_expected_names():
    assert specialists.search_flights.name == "search_flights"
    assert specialists.search_hotels.name == "search_hotels"
    assert specialists.search_activities.name == "search_activities"
