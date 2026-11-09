"""Result exploration must use the entire saved result set, never Google."""

from copy import deepcopy

import pytest

from flightcore.flexible import SearchValidationError
from flightcore.result_browser import ResultFilters
from tests.test_flexible import make_manager, request_spec


def stored_trips(tmp_path, count=1, spec=None):
    manager, live, jid = make_manager(tmp_path, spec)
    manager.run(jid)
    job = manager.get(jid)
    original = next(iter(job["results"].values()))
    trips = []
    for i in range(count):
        trip = deepcopy(original)
        trip["id"] = f"trip-{i:03d}"
        trip["price"] = 40 + i
        trips.append(trip)
    job["results"] = {t["id"]: t for t in trips}
    manager.save(job)
    return manager, live, jid, trips


def save_trips(manager, jid, trips):
    job = manager.get(jid)
    job["results"] = {t["id"]: t for t in trips}
    manager.save(job)


def test_filter_and_groups_cover_results_beyond_first_page(tmp_path):
    manager, live, jid, trips = stored_trips(tmp_path, 125)
    for trip in trips[80:]:
        trip["outbound"]["segments"][-1]["to_code"] = "MAD"
        trip["inbound"]["segments"][0]["from_code"] = "MAD"
    save_trips(manager, jid, trips)
    calls = list(live.calls)
    all_results = manager.public(jid)
    assert len(all_results["results"]) == 50
    assert [(g["destination"], g["count"]) for g in all_results["groups"]] == [
        ("BCN", 80),
        ("MAD", 45),
    ]
    filtered = manager.public(jid, filters=ResultFilters(destination="MAD", max_price=125))
    assert filtered["result_count"] == 125
    assert filtered["filtered_count"] == 6
    assert filtered["results"][0]["id"] == "trip-080"
    assert filtered["groups"][0]["cheapest"]["price"] == 120
    assert filtered["calendar"][0]["price"] == 120
    assert filtered["facets"]["destinations"] == ["BCN", "MAD"]
    assert filtered["airports"]["MAD"]["city"] == "Madrid"
    assert live.calls == calls


def test_airline_filter_requires_every_segment_including_return(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 2)
    trips[1]["inbound"]["segments"][0]["airline_code"] = "VY"
    trips[1]["inbound"]["segments"][0]["airline_name"] = "Vueling"
    save_trips(manager, jid, trips)
    job = manager.public(jid, filters=ResultFilters(airline="FR"))
    assert job["filtered_count"] == 1 and job["results"][0]["id"] == "trip-000"
    assert {a["code"] for a in job["facets"]["airlines"]} == {"FR", "VY"}


def test_combined_filters_and_empty_summaries(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 3)
    trips[0]["useful_minutes"] = 35 * 60
    trips[1]["useful_minutes"] = 48 * 60
    trips[2]["useful_minutes"] = 60 * 60
    trips[2]["outbound"]["segments"][0]["from_code"] = "TSF"
    save_trips(manager, jid, trips)
    job = manager.public(jid, filters=ResultFilters(min_hours=36, max_price=41, origin="VCE"))
    assert [t["id"] for t in job["results"]] == ["trip-001"]
    empty = manager.public(jid, filters=ResultFilters(excluded=("BCN",)))
    assert empty["result_count"] == 3 and empty["filtered_count"] == 0
    assert empty["groups"] == empty["results"] == empty["calendar"] == []
    assert empty["recommendations"] == {}


def test_return_deadline_rejects_next_day_even_with_early_clock(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 3)
    trips[0]["inbound"]["segments"][-1]["arrival"] = "2026-10-04T23:00:00+02:00"
    trips[1]["inbound"]["segments"][-1]["arrival"] = "2026-10-05T01:00:00+02:00"
    trips[2]["inbound"]["segments"][-1]["arrival"] = "2026-10-04T23:01:00+02:00"
    save_trips(manager, jid, trips)
    job = manager.public(jid, filters=ResultFilters(home_before="23:00"))
    assert [t["id"] for t in job["results"]] == ["trip-000"]


def test_recommendation_explains_extra_price_and_time(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 4)
    for trip, price, hours in zip(trips, [40, 52, 60, 150], [30, 39, 31, 60], strict=True):
        trip["price"] = price
        trip["useful_minutes"] = hours * 60
    save_trips(manager, jid, trips)
    rec = manager.public(jid)["recommendations"]
    assert rec["cheapest"]["id"] == "trip-000"
    assert rec["longest"]["id"] == "trip-003"
    assert rec["balanced"]["id"] == "trip-001"
    assert rec["balanced"]["extra_price"] == 12
    assert rec["balanced"]["extra_minutes"] == 9 * 60
    filtered = manager.public(jid, filters=ResultFilters(max_price=40))
    assert "balanced" not in filtered["recommendations"]


def test_open_jaw_not_promoted_as_useful_destination_time(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 2)
    trips[0]["destination_airport_change"] = True
    trips[0]["useful_minutes"] = 100 * 60
    save_trips(manager, jid, trips)
    rec = manager.public(jid)["recommendations"]
    assert rec["cheapest"]["id"] == "trip-000"
    assert rec["longest"]["id"] == "trip-001"
    assert "balanced" not in rec


def test_focus_finds_recommendation_on_later_page_and_clamps_offset(tmp_path):
    manager, _live, jid, _trips = stored_trips(tmp_path, 125)
    job = manager.public(jid, focus="trip-123")
    assert job["offset"] == 100 and job["focused_id"] == "trip-123"
    assert "trip-123" in {t["id"] for t in job["results"]}
    job = manager.public(jid, offset=100, filters=ResultFilters(max_price=41), focus="trip-123")
    assert job["offset"] == 0 and job["focused_id"] is None
    assert job["filtered_count"] == 2


def test_one_way_has_only_price_recommendation(tmp_path):
    manager, _live, jid, _trips = stored_trips(tmp_path, spec=request_spec(trip_type="one_way"))
    job = manager.public(jid)
    assert set(job["recommendations"]) == {"cheapest"}
    assert job["groups"][0]["cheapest"]["useful_minutes"] is None


def test_recheck_removal_updates_groups_recommendations_and_facets(tmp_path):
    manager, live, jid, _trips = stored_trips(tmp_path)
    live.remove = True
    assert manager.recheck(jid, "trip-000")["status"] == "not_confirmed"
    job = manager.public(jid)
    assert job["groups"] == [] and job["recommendations"] == {}
    assert job["facets"]["destinations"] == []
    assert job["filtered_count"] == job["result_count"] == 0


@pytest.mark.parametrize(
    "query",
    [
        {"max_price": "NaN"},
        {"max_price": "inf"},
        {"min_hours": "-1"},
        {"home_before": "24:00"},
        {"origin": "invalid"},
        {"airline": "<x>"},
        {"exclude": "BCN,bad-code"},
    ],
)
def test_invalid_result_filters_rejected(query):
    with pytest.raises(SearchValidationError):
        ResultFilters.parse(query)


def test_http_browsing_is_read_only_and_validates_query(monkeypatch, tmp_path):
    from app import app
    from flightcore import flexible_api

    manager, live, jid, _trips = stored_trips(tmp_path, 60)
    monkeypatch.setattr(flexible_api, "manager", manager)
    client = app.test_client()
    calls = list(live.calls)
    response = client.get(f"/api/search/jobs/{jid}?max_price=45&origin=VCE")
    assert response.status_code == 200 and response.json["filtered_count"] == 6
    assert response.json["result_count"] == 60
    assert client.get(f"/api/search/jobs/{jid}?min_hours=NaN").status_code == 400
    assert live.calls == calls


def test_suggestions_and_group_order_use_best_trip_beyond_first_page(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 70)
    for trip in trips:
        trip["price"] = 50
        trip["useful_minutes"] = 30 * 60
    last = trips[-1]
    last["price"] = 60
    last["useful_minutes"] = 50 * 60
    last["outbound"]["segments"][-1]["to_code"] = "MAD"
    last["inbound"]["segments"][0]["from_code"] = "MAD"
    save_trips(manager, jid, trips)
    job = manager.public(jid)
    assert "trip-069" not in {t["id"] for t in job["results"]}
    assert job["recommendations"]["longest"]["id"] == "trip-069"
    assert job["recommendations"]["balanced"]["id"] == "trip-069"
    ranked = manager.public(jid, sort="useful")
    assert ranked["groups"][0]["destination"] == "MAD"
    assert ranked["groups"][0]["featured"]["useful_minutes"] == 50 * 60


def test_groups_by_city_and_filter_covers_all_its_airports(tmp_path):
    manager, _live, jid, trips = stored_trips(tmp_path, 3)
    for trip, airport in zip(trips, ["STN", "LTN", "GDN"], strict=True):
        trip["outbound"]["segments"][-1]["to_code"] = airport
    save_trips(manager, jid, trips)
    job = manager.public(jid)
    assert [(g["city"], g["destination"], g["count"]) for g in job["groups"]] == [
        ("Londra", "LTN,STN", 2),
        ("Danzica", "GDN", 1),
    ]
    assert job["groups"][0]["cheapest"]["price"] == 40
    london = ResultFilters.parse({"destination": "ltn,STN"})
    assert manager.public(jid, filters=london)["filtered_count"] == 2
    assert manager.public(jid, filters=ResultFilters.parse({"exclude": "LTN,STN"}))[
        "filtered_count"
    ] == 1
    with pytest.raises(SearchValidationError):
        ResultFilters.parse({"destination": "STN,LONDON"})


def test_pdf_export_follows_filters_without_calling_google(monkeypatch, tmp_path):
    from app import app
    from flightcore import flexible_api
    from flightcore.pdf_export import by_city

    manager, live, jid, trips = stored_trips(tmp_path, 8)
    for trip, airport in zip(trips, ["STN", "LTN", "STN", "GDN"] * 2, strict=True):
        trip["outbound"]["segments"][-1]["to_code"] = airport
    save_trips(manager, jid, trips)
    monkeypatch.setattr(flexible_api, "manager", manager)
    client = app.test_client()
    calls = list(live.calls)
    response = client.get(f"/api/search/jobs/{jid}/export.pdf?exclude=GDN")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF") and "attachment" in response.headers[
        "Content-Disposition"
    ]
    _spec, exported = manager.export(jid, ResultFilters.parse({"exclude": "GDN"}))
    cities = by_city(exported)
    assert [(c["name"], c["codes"], c["count"]) for c in cities] == [("Londra", ["LTN", "STN"], 6)]
    # Same weekend on every copy here: one line per distinct weekend.
    assert len(cities[0]["trips"]) == 1 and cities[0]["trips"][0]["price"] == 40
    assert client.get(f"/api/search/jobs/{jid}/export.pdf?max_price=1").status_code == 200
    assert client.get(f"/api/search/jobs/{jid}/export.pdf?min_hours=NaN").status_code == 400
    assert live.calls == calls
