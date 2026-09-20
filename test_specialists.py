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
