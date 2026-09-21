import json

import specialists
from specialists import _compact_hours, _parse_price, _search_flights, _search_hotels, _search_places, _search_sights


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
         "arrive_at": "2026-10-10 13:00", "stops": 1, "duration_min": 300, "airline_logo": ""},
        {"total": 99.0, "currency": "USD", "airlines": ["Vueling"], "depart_at": "2026-10-10 18:00",
         "arrive_at": "2026-10-10 20:10", "stops": 0, "duration_min": 130, "airline_logo": ""},
    ]


def test_search_flights_passes_api_error_through(monkeypatch):
    fake_serpapi(monkeypatch, {"error": "Google Flights hasn't returned any results for this query."})
    assert json.loads(_search_flights("MAD", "CDG", "2026-10-10", "2026-10-14", 1, "USD")) == {"error": "Google Flights hasn't returned any results for this query."}


def test_search_hotels_reshapes_and_skips_unpriced(monkeypatch):
    calls = fake_serpapi(monkeypatch, {"properties": [
        {"name": "Hotel A", "total_rate": {"extracted_lowest": 400}, "rate_per_night": {"extracted_lowest": 100},
         "hotel_class": "4-star hotel", "overall_rating": 4.4, "reviews": 120, "description": "Near the Louvre",
         "amenities": ["Wi-Fi", "Pool"], "nearby_places": [{"name": "Louvre"}], "images": [{"thumbnail": "http://img/a.jpg"}]},
        {"name": "Sold out", "total_rate": {}},
    ]})
    out = json.loads(_search_hotels("Paris", "2026-10-10", "2026-10-14", 1, "USD", 120))
    assert calls[0]["q"] == "Paris hotels" and calls[0]["max_price"] == 120 and calls[0]["currency"] == "USD"
    assert out == [{"name": "Hotel A", "total": 400.0, "nightly": 100, "currency": "USD", "stars": "4-star hotel",
                    "rating": 4.4, "reviews": 120, "description": "Near the Louvre", "amenities": ["Wi-Fi", "Pool"],
                    "nearby": ["Louvre"], "image": "http://img/a.jpg"}]


def test_parse_price():
    assert _parse_price("$25") == (25.0, "USD")
    assert _parse_price("From €1,200.50") == (1200.5, "EUR")
    assert _parse_price("Free") == (0.0, "USD")
    assert _parse_price(None) is None
    assert _parse_price("Varies") is None


def test_search_sights_converts_currency_and_skips_unpriced(monkeypatch):
    fake_serpapi(monkeypatch, {"top_sights": {"sights": [
        {"title": "Louvre", "rating": 4.7, "reviews": 90000, "price": "$20", "thumbnail": "http://img/l.jpg", "link": "http://g/louvre"},
        {"title": "Seine walk"},
    ]}})
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: 2.0)
    out = json.loads(_search_sights("Paris", "INR"))
    assert out == [{"name": "Louvre", "rating": 4.7, "reviews": 90000, "price": 40.0, "currency": "INR", "image": "http://img/l.jpg", "link": "http://g/louvre"}]


def test_search_sights_keeps_source_currency_when_fx_fails(monkeypatch):
    fake_serpapi(monkeypatch, {"top_sights": {"sights": [{"title": "X", "price": "€5"}]}})
    monkeypatch.setattr(specialists, "fx_rate", lambda s, d: None)
    out = json.loads(_search_sights("Paris", "INR"))
    assert out[0]["price"] == 5.0 and out[0]["currency"] == "EUR"


def test_search_places_reshapes_and_sorts_best_first(monkeypatch):
    calls = fake_serpapi(monkeypatch, {"local_results": [
        {"title": "Meh Shack", "type": "Restaurant", "rating": 4.1, "reviews": 2000, "address": "Baga, Goa", "open_state": "Open"},
        {"title": "Prince of Sal Water Sports", "type": "Water sports equipment rental service", "rating": "4.8", "reviews": "1,687",
         "address": "Mobor, Goa", "operating_hours": {"monday": "9 AM–6 PM", "tuesday": "9 AM–6 PM"}, "thumbnail": "http://img/p.jpg", "place_id": "ChIJabc"},
        {"rating": "4.0"},
    ]})
    out = json.loads(_search_places("parasailing Candolim", "Goa"))
    assert calls[0]["engine"] == "google_maps" and calls[0]["q"] == "parasailing Candolim in Goa"
    assert out[0] == {"name": "Prince of Sal Water Sports", "category": "Water sports equipment rental service", "rating": 4.8,
                      "reviews": 1687, "address": "Mobor, Goa", "hours": "daily 9 AM–6 PM", "image": "http://img/p.jpg",
                      "link": "https://www.google.com/maps/place/?q=place_id:ChIJabc"}
    assert out[1]["name"] == "Meh Shack" and out[1]["hours"] == "Open" and len(out) == 2


def test_compact_hours():
    assert _compact_hours(None) == ""
    assert _compact_hours({"monday": "9 AM–6 PM", "sunday": "9 AM–6 PM"}) == "daily 9 AM–6 PM"
    assert _compact_hours({"monday": "Closed", "tuesday": "10 AM–8 PM"}) == "Mon Closed; Tue 10 AM–8 PM"


def test_tools_have_expected_names():
    assert specialists.search_flights.name == "search_flights"
    assert specialists.search_hotels.name == "search_hotels"
    assert specialists.search_sights.name == "search_sights"
    assert specialists.search_places.name == "search_places"
