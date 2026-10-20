"""One engine contract: trip modes, arbitrary airports, new filters and old jobs."""

from dataclasses import replace
from datetime import date

import pytest

from flightcore.flexible import FlexibleRequest, SearchValidationError, build_plan, matches
from flightcore.live_quotes import LiveQuotes
from tests.test_flexible import html_payload, make_manager, quote, request_spec, wire_row


def test_fixed_dates_ignore_weekdays_and_flexible_nights():
    r = request_spec(
        date_mode="fixed",
        start_date="2026-10-01",
        end_date="2026-10-12",
        departure_days=[],
        return_days=[],
        min_nights=30,
        max_nights=1,
    )
    assert r.date_pairs() == [("2026-10-01", "2026-10-12")]


def test_one_way_fixed_date_needs_only_departure():
    r = FlexibleRequest.parse(
        {
            "trip_type": "one_way",
            "date_mode": "fixed",
            "start_date": "2026-10-01",
            "origins": ["FCO"],
            "destinations": ["MXP"],
        },
        today=date(2026, 9, 16),
    )
    plan = build_plan(r, {})
    assert plan["date_pairs"] == [("2026-10-01", None)]
    assert plan["distinct_searches"] == plan["searches_upper_bound"] == 1


def test_one_way_flexible_uses_departure_days_only():
    r = request_spec(trip_type="one_way", departure_days=[3, 4], return_days=[], max_nights=0)
    assert r.date_pairs() == [("2026-10-01", None), ("2026-10-02", None)]


def test_same_day_round_trip_allowed():
    r = request_spec(min_nights=0, max_nights=0, return_days=[4])
    assert r.date_pairs() == [("2026-10-02", "2026-10-02")]


def test_multiple_airports_normalized_without_excluding_cross_routes():
    r = request_spec(origins="FCO, CIA, fco", destinations=["CIA", "MXP"])
    plan = build_plan(r, {})
    routes = {(task[3], task[2]) for task in plan["tasks"]}
    assert routes == {("FCO", "CIA"), ("FCO", "MXP"), ("CIA", "MXP")}


def test_curated_destinations_and_anywhere_fallback_are_explicit(monkeypatch):
    from flightcore import flexible_api

    r = request_spec(origins=["FCO"], destinations=[], destination_set="city-break")
    curated = build_plan(r, flexible_api.candidate_routes(r))
    assert "Weekend in citta'" in curated["coverage"]
    assert "BCN" in curated["destinations"]
    monkeypatch.setattr(flexible_api.routes, "destinations", lambda _origin: [])
    r = replace(r, destination_set="real")
    candidates = flexible_api.candidate_routes(r)
    assert "catalogo_generale" in candidates
    assert "non esaustiva" in build_plan(r, candidates)["coverage"]


def test_explicit_destinations_win_over_a_curated_pool():
    from flightcore import flexible_api

    r = request_spec(destinations=["BCN"], destination_set="long-haul")
    assert build_plan(r, flexible_api.candidate_routes(r))["destinations"] == ["BCN"]


@pytest.mark.parametrize(
    "fields",
    [
        {"trip_type": "invalid"},
        {"date_mode": "invalid"},
        {"origins": ["FCO"] * 11},
        {"infants": 2, "adults": 1},
        {"seat": "invalid"},
        {"airlines": ["Ryanair"]},
        {"departure_duration_min": 200, "departure_duration_max": 120},
        {"min_layover_minutes": 100, "max_layover_minutes": 30},
        {"return_departure": "group", "destinations": []},
        {"allow_airport_change": "false"},
        {"schengen_transfers_only": "true"},
    ],
)
def test_invalid_new_constraints_are_rejected(fields):
    with pytest.raises(SearchValidationError):
        request_spec(**fields)


def test_one_way_worker_recheck_and_calendar_never_query_return(tmp_path):
    r = request_spec(trip_type="one_way", budget=30)
    manager, live, job_id = make_manager(tmp_path, r)
    manager.run(job_id)
    job = manager.public(job_id)
    assert job["status"] == "complete" and job["result_count"] == 1
    trip = job["results"][0]
    assert trip["inbound"] is None and trip["useful_minutes"] is None
    assert job["calendar"] == [{"date": "2026-10-02", "return_date": None, "price": 30}]
    assert len(live.calls) == 1
    assert manager.recheck(job_id, trip["id"])["status"] == "confirmed"
    assert len(live.calls) == 2 and all(not call[3] for call in live.calls)
    live.new_price = 40
    assert manager.recheck(job_id, trip["id"])["status"] == "outside_filters"
    assert manager.public(job_id)["calendar"] == []


def test_open_jaw_checks_every_allowed_return_and_preserves_total(tmp_path):
    r = request_spec(
        origins=["VCE", "TSF"],
        destinations=["BCN", "MAD"],
        return_airport="group",
        return_departure="group",
    )
    manager, live, job_id = make_manager(tmp_path, r)
    manager.run(job_id)
    job = manager.public(job_id)
    assert job["status"] == "complete" and job["result_count"] == 16
    assert {(x[0], x[1]) for x in live.calls if x[3]} == {
        ("BCN", "VCE"),
        ("BCN", "TSF"),
        ("MAD", "VCE"),
        ("MAD", "TSF"),
    }
    assert all(trip["price"] == 60 for trip in job["results"])
    assert sum(trip["destination_airport_change"] for trip in job["results"]) == 8


def connecting_quote(via="MAD", *, next_airport=None):
    q = quote(destination=via, departure="18:00", arrival="19:00")
    q["segments"] += quote(origin=next_airport or via, departure="20:00", arrival="21:00")[
        "segments"
    ]
    q["duration_min"] = 180
    q["self_transfer"] = False
    return q


def test_layover_and_duration_bounds_enforced_on_live_timestamps():
    q = connecting_quote()
    r = request_spec(
        max_stops=1,
        min_layover_minutes=60,
        max_layover_minutes=60,
        departure_duration_min=180,
        departure_duration_max=180,
    )
    assert matches(q, r, "VCE", "BCN", "2026-10-02")
    assert not matches(q, replace(r, max_layover_minutes=59), "VCE", "BCN", "2026-10-02")
    assert not matches(q, replace(r, min_layover_minutes=61), "VCE", "BCN", "2026-10-02")
    assert not matches(q, replace(r, departure_duration_max=179), "VCE", "BCN", "2026-10-02")


def test_airport_change_is_separate_from_self_transfer():
    q = connecting_quote("CDG", next_airport="ORY")
    q["self_transfer"] = True
    r = request_spec(max_stops=1, allow_self_transfer=True)
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    assert matches(q, replace(r, allow_airport_change=True), "VCE", "BCN", "2026-10-02")
    assert not matches(
        q,
        replace(r, allow_airport_change=True, allow_self_transfer=False),
        "VCE",
        "BCN",
        "2026-10-02",
    )


def test_schengen_filter_only_checks_connections():
    r = request_spec(max_stops=1, schengen_transfers_only=True)
    assert matches(connecting_quote(), r, "VCE", "BCN", "2026-10-02")
    assert not matches(connecting_quote("LHR"), r, "VCE", "BCN", "2026-10-02")
    assert matches(quote(destination="LHR"), r, "VCE", "LHR", "2026-10-02")


def test_arrival_clock_and_one_way_final_day_deadline():
    q = quote(arrival="20:30")
    r = request_spec(
        trip_type="one_way", departure_arrival_min="20:30", departure_arrival_max="20:30"
    )
    assert matches(q, r, "VCE", "BCN", "2026-10-02")
    assert not matches(q, replace(r, final_arrival_max="20:29"), "VCE", "BCN", "2026-10-02")
    q["segments"][0]["arrival"] = "2026-10-03T20:30:00+02:00"
    assert not matches(q, replace(r, final_arrival_max="23:59"), "VCE", "BCN", "2026-10-02")


def test_airline_filter_applies_to_all_segments():
    q = connecting_quote()
    q["segments"][1]["airline_code"] = "IB"
    r = request_spec(max_stops=1, airlines="FR")
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    assert matches(q, replace(r, airlines=("FR", "IB")), "VCE", "BCN", "2026-10-02")


def test_google_query_and_cache_separate_cabin_infants_and_airlines(monkeypatch):
    live = LiveQuotes()
    queries = []

    def fetch(query):
        queries.append(query.pb())
        return html_payload([wire_row()])

    monkeypatch.setattr(live, "_fetch", fetch)
    r = request_spec()
    for spec in (
        r,
        replace(r, seat="business"),
        replace(r, infants=1),
        replace(r, airlines=("FR",)),
        replace(r, exclude_basic_economy=True),
    ):
        result = live.search("VCE", "BCN", "2026-10-02", spec)
        assert result["quotes"][0]["travelers"] == 1 + spec.infants
    assert len(queries) == 5
    assert queries[0].SerializeToString() != queries[1].SerializeToString()
    assert queries[0].SerializeToString() != queries[2].SerializeToString()
    assert list(queries[3].data[0].airlines) == ["FR"]


def test_old_job_without_new_fields_resumes(tmp_path):
    manager, _live, job_id = make_manager(tmp_path)
    job = manager.get(job_id)
    for key in ("trip_type", "date_mode", "seat", "return_departure", "infants"):
        job["request"].pop(key)
    manager.save(job)
    manager.run(job_id)
    assert manager.public(job_id)["result_count"] == 1


def test_all_entry_points_use_same_worker_and_calendar(monkeypatch, tmp_path, frozen_today):
    from app import app
    from flightcore import flexible_api

    manager, live, _job_id = make_manager(tmp_path)
    monkeypatch.setattr(flexible_api, "manager", manager)
    client = app.test_client()
    for path in ("/api/search/jobs", "/api/flexible/jobs"):
        response = client.post(path, json=request_spec(trip_type="one_way").to_dict())
        assert response.status_code == 202
        manager.run(response.json["id"])
    for path in ("/api/search", "/api/explore"):
        response = client.post(
            path, json={"origin": "FCO", "destination": "BCN", "departure_date": "2026-10-01"}
        )
        assert response.status_code == 202
        manager.run(response.json["id"])
        assert manager.public(response.json["id"])["result_count"] == 1
    response = client.post(
        "/api/cheapest-dates",
        json={
            "origin": "FCO",
            "destination": "BCN",
            "from_date": "2026-10-01",
            "to_date": "2026-10-03",
        },
    )
    assert response.status_code == 202
    manager.run(response.json["id"])
    assert len(manager.public(response.json["id"])["calendar"]) == 3
    assert all(not call[3] for call in live.calls)
