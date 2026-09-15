"""Contract tests: exact weekends, complete quotes, UTC elapsed time, saved job lifecycle.

Synthetic Google rows below contain no real tokens, cookies or network calls.
"""

import base64
import json
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest

from flightcore.flexible import (
    FlexibleRequest,
    SearchValidationError,
    build_plan,
    combine,
    local_instant,
    matches,
)
from flightcore.flexible_jobs import JobManager
from flightcore.live_quotes import LiveBlocked, LiveQuotes, LiveUnavailable, decode


def request_spec(**extra):
    values = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-04",
        "origins": ["VCE"],
        "destinations": ["BCN"],
    }
    values.update(extra)
    return FlexibleRequest.parse(values, today=date(2026, 9, 15))


def quote(
    origin="VCE",
    destination="BCN",
    day="2026-10-02",
    departure="18:30",
    arrival="20:30",
    price=30,
    flight="1234",
):
    return {
        "segments": [
            {
                "from_code": origin,
                "to_code": destination,
                "departure": local_instant(f"{day}T{departure}:00", origin).isoformat(),
                "arrival": local_instant(f"{day}T{arrival}:00", destination).isoformat(),
                "airline_code": "FR",
                "airline_name": "Ryanair",
                "flight_number": flight,
                "duration_min": 120,
            }
        ],
        "price": price,
        "currency": "EUR",
        "duration_min": 120,
        "self_transfer": None,
        "baggage": "base",
        "travelers": 1,
        "verification": "google_live",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "booking_url": "https://www.google.com/travel/flights",
        "airline_verified": False,
    }


def returning(**kw):
    return quote(origin="BCN", destination="VCE", day="2026-10-04", **kw)


def test_quarter_generates_exact_friday_sunday_pairs():
    r = request_spec(end_date="2026-12-31")
    assert len(r.date_pairs()) == 13
    assert r.date_pairs()[0] == ("2026-10-02", "2026-10-04")
    assert r.date_pairs()[-1] == ("2026-12-25", "2026-12-27")
    assert all(
        date.fromisoformat(a).weekday() == 4 and date.fromisoformat(b).weekday() == 6
        for a, b in r.date_pairs()
    )


def test_range_includes_both_travel_dates_not_only_outbound():
    with pytest.raises(SearchValidationError, match="Nessuna coppia"):
        request_spec(end_date="2026-10-03")


def test_flexible_thursday_sunday_and_three_to_five_nights():
    r = request_spec(
        departure_days=[3], return_days=[6, 0, 1], min_nights=3, max_nights=5, end_date="2026-10-06"
    )
    assert r.date_pairs() == [
        ("2026-10-01", "2026-10-04"),
        ("2026-10-01", "2026-10-05"),
        ("2026-10-01", "2026-10-06"),
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"budget": "NaN"},
        {"budget": float("inf")},
        {"budget": -2},
        {"budget": True},
        {"departure_min": "18:75"},
        {"return_max": "24:00"},
        {"origins": []},
        {"origins": ["NOPE"]},
        {"adults": 1.5},
        {"adults": 9, "children": 1},
        {"departure_days": []},
        {"return_days": [7]},
        {"allow_self_transfer": "false"},
        {"departure_min": "23:00", "departure_max": "18:00"},
        {"min_nights": 5, "max_nights": 2},
        {"destinations": ["ZZZ"]},
        {"start_date": "2025-01-01"},
        {"end_date": "2028-01-01"},
    ],
)
def test_bad_input_fails_before_any_network(fields):
    with pytest.raises(SearchValidationError):
        request_spec(**fields)


def test_minute_precision_rejects_1800_when_user_asks_1830():
    r = request_spec(departure_min="18:30")
    assert not matches(quote(departure="18:29"), r, "VCE", "BCN", "2026-10-02")
    assert matches(quote(departure="18:30"), r, "VCE", "BCN", "2026-10-02")


def test_wrong_date_airport_number_currency_or_unpriced_never_passes():
    r = request_spec()
    for key, value in [
        ("price", None),
        ("currency", "USD"),
        ("verification", "schedule"),
        ("travelers", 2),
        ("baggage", "cabin"),
    ]:
        q = quote()
        q[key] = value
        assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    q = quote()
    q["segments"][0]["flight_number"] = ""
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    assert not matches(quote(), r, "TSF", "BCN", "2026-10-02")
    assert not matches(quote(), r, "VCE", "BCN", "2026-10-09")


def test_sunday_late_departure_monday_arrival_excluded_with_deadline():
    q = returning(departure="23:30")
    q["segments"][0]["arrival"] = "2026-10-05T02:00:00+02:00"
    assert matches(q, request_spec(), "BCN", "VCE", "2026-10-04", True)
    assert not matches(q, request_spec(final_arrival_max="23:59"), "BCN", "VCE", "2026-10-04", True)


def test_roundtrip_budget_and_two_ticket_disclosure():
    assert combine(quote(price=60), returning(price=45), request_spec()) is None
    p = combine(quote(price=44), returning(price=45), request_spec())
    assert p["price"] == 89
    assert p["nights"] == 2
    assert p["ticketing"] == "separate_one_way"
    assert p["airline_verified"] is False
    assert p["useful_minutes"] == 46 * 60


def test_useful_time_measures_utc_across_dst():
    a = quote(day="2026-10-23", arrival="20:00")
    b = quote(origin="BCN", destination="VCE", day="2026-10-25", departure="18:00")
    assert combine(a, b, request_spec())["useful_minutes"] == 47 * 60


def test_ambiguous_dst_time_is_not_guessed():
    with pytest.raises(ValueError, match="ambiguo"):
        local_instant("2026-10-25T02:30:00", "VCE")


def test_cross_timezone_flight_arrival_is_not_subtracted_as_local_time():
    q = quote(destination="LHR", departure="18:30", arrival="19:30")
    seg = q["segments"][0]
    elapsed = datetime.fromisoformat(seg["arrival"]) - datetime.fromisoformat(seg["departure"])
    assert elapsed.total_seconds() == 2 * 3600


def test_minimum_time_at_destination():
    assert combine(quote(), returning(), request_spec(min_destination_hours=47)) is None


def test_unknown_connection_protection_excluded_unless_opted_in():
    q = quote(destination="MAD", arrival="19:30")
    q["segments"].extend(quote(origin="MAD", departure="20:30", arrival="21:30")["segments"])
    r = request_spec(max_stops=1)
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    assert matches(q, replace(r, allow_self_transfer=True), "VCE", "BCN", "2026-10-02")
    q["self_transfer"] = False
    assert matches(q, r, "VCE", "BCN", "2026-10-02")
    q["self_transfer"] = True
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")


def test_low_cost_filter_checks_each_segment():
    q = quote()
    q["segments"][0]["airline_code"] = "BA"
    assert not matches(q, request_spec(airline_mode="low_cost"), "VCE", "BCN", "2026-10-02")


def test_plan_does_not_treat_missing_route_as_proof_of_no_service():
    r = request_spec(origins=["VCE", "TSF"], destinations=[], return_airport="group")
    p = build_plan(r, {"VCE": ["BCN"], "TSF": ["LHR"]})
    assert len(p["tasks"]) == 4
    assert p["distinct_searches"] == 8
    assert p["searches_upper_bound"] == 12  # repeated returns may expire during a long run
    assert p["candidate_count"] == 2


def wire_row(price=30, currency="EUR"):
    fl = [None] * 33
    fl[3:7] = ["VCE", "Venezia", "Barcellona", "BCN"]
    fl[8], fl[10], fl[11] = [18], [20], 120
    fl[17] = "Boeing 737"
    fl[20], fl[21], fl[22] = [2026, 10, 2], [2026, 10, 2], ["FR", "1234"]
    detail = [None] * 25
    detail[0], detail[1], detail[2], detail[9] = "FR", ["Ryanair"], [fl], 120
    token = base64.b64encode(b"\x1a\x05\x1a\x03" + currency.encode()).decode()
    return [detail, [[None, price], token], None, None, None, None, None, None, "synthetic-token"]


def html_payload(best=None, other=None, airlines=None):
    p = [None] * 8
    p[2], p[3] = [best], [other]
    p[7] = [None, [None, airlines]]
    return (
        '<script class="ds:1">AF_initDataCallback({data:'
        + json.dumps(p)
        + ",sideChannel:{}});</script>"
    )


def test_decoder_combines_best_and_other_flights_deduplicates_and_keeps_number():
    row = wire_row()
    qs, rejected = decode(html_payload([row], [row]), request_spec(), "https://www.google.com")
    assert rejected == 0 and len(qs) == 1
    assert qs[0]["segments"][0]["flight_number"] == "1234"
    assert qs[0]["segments"][0]["departure"] == "2026-10-02T18:00:00+02:00"
    assert qs[0]["currency"] == "EUR"


def test_decoder_missing_identity_or_live_price_is_not_normal_result():
    missing_id, missing_price = wire_row(), wire_row()
    missing_id[0][2][0][22] = ["FR", None]
    missing_price[1][0] = []
    quotes, rejected = decode(html_payload([missing_id], [missing_price]), request_spec(), "")
    assert quotes == [] and rejected == 2


def test_decoder_bad_currency_excluded_and_midnight_zero_components_supported():
    row = wire_row()
    row[0][2][0][8] = [None, 31]
    quotes, rejected = decode(html_payload([row], [wire_row(currency="USD")]), request_spec(), "")
    assert len(quotes) == 1 and rejected == 1
    assert "T00:31:" in quotes[0]["segments"][0]["departure"]


def test_empty_vs_broken_page_distinguished():
    assert decode(html_payload(), request_spec(), "") == ([], 0)
    with pytest.raises(LiveUnavailable):
        decode("<html>captcha</html>", request_spec(), "")
    with pytest.raises(LiveUnavailable):
        decode(
            '<script class="ds:1">AF_initDataCallback({data:[],sideChannel:{}});</script>',
            request_spec(),
            "",
        )


def null_results_payload(origin="VCE", destination="ADB", *, suggestions=True):
    """Minimal sanitized shape observed for VCE/TSF-ADB on 2026-09-18.

    Google showed 'Nessuna opzione corrisponde ai tuoi filtri' in the browser.
    Unlike the existing synthetic fixture, the two result groups are null.
    Request/session strings below are synthetic, not captured tokens.
    """
    payload = [None] * 24
    payload[0] = [None, [[1, 2, 3]], 0, "synthetic-request", "synthetic-session"]
    payload[1] = [[[[[origin, 0], "Origin airport"]], [[[destination, 0], "Destination airport"]]]]
    payload[7] = [[], [[], []]]
    payload[12] = ["synthetic-search-context", 0]
    payload[23] = 1
    if suggestions:
        payload[6] = [
            [
                None,
                None,
                None,
                None,
                [
                    [
                        ["2026-09-17", None, [None, 120], ["synthetic-other-date"]],
                        ["2026-09-20", None, [None, 110], ["synthetic-other-date"]],
                    ]
                ],
            ]
        ]
    return payload


def shopping_html(payload):
    return (
        '<script class="ds:1">AF_initDataCallback({data:'
        + json.dumps(payload)
        + ",sideChannel:{}});</script>"
    )


@pytest.mark.parametrize("origin,suggestions", [("VCE", True), ("TSF", False)])
def test_null_groups_with_normal_search_context_are_empty_not_an_error(origin, suggestions):
    payload = null_results_payload(origin, suggestions=suggestions)
    assert decode(shopping_html(payload), request_spec(), "", expected_route=(origin, "ADB")) == (
        [],
        0,
    )  # suggested flights on other dates never enter the results


def test_null_groups_accept_a_resolved_iata_missing_from_fli_static_enum():
    """A valid Google envelope must not depend on fli's stale airport enum."""
    payload = null_results_payload("VCE", "KIV")
    assert decode(
        shopping_html(payload),
        request_spec(),
        "",
        expected_route=("VCE", "KIV"),
    ) == ([], 0)


@pytest.mark.parametrize(
    "slot,replacement",
    [
        (0, None),
        (0, [None, [], 1]),
        (1, []),
        (7, None),
        (12, []),
        (12, ["error-context", 1]),
        (23, None),
        (2, {}),
    ],
)
def test_broken_or_error_envelopes_are_not_misreported_as_no_flights(slot, replacement):
    payload = null_results_payload()
    payload[slot] = replacement
    with pytest.raises(LiveUnavailable):
        decode(shopping_html(payload), request_spec(), "", expected_route=("VCE", "ADB"))


def test_empty_response_for_another_route_is_not_accepted():
    with pytest.raises(LiveUnavailable):
        decode(
            shopping_html(null_results_payload()),
            request_spec(),
            "",
            expected_route=("VCE", "BCN"),
        )


def test_absent_groups_without_search_metadata_are_not_assumed_empty():
    with pytest.raises(LiveUnavailable):
        decode(shopping_html([None] * 24), request_spec(), "")


@pytest.mark.parametrize("status,traffic", [(403, False), (429, False), (200, True)])
def test_http_blocks_still_stop_before_decoding_and_do_not_retry(monkeypatch, status, traffic):
    import primp

    calls = []
    html = (
        "Our systems have detected unusual traffic"
        if traffic
        else shopping_html(null_results_payload())
    )

    def get(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=status, text=html)

    monkeypatch.setattr(primp, "Client", lambda **kw: SimpleNamespace(get=get))
    live = LiveQuotes()
    r = request_spec()
    for _ in range(2):
        with pytest.raises(LiveBlocked):
            live.search("VCE", "ADB", "2026-09-18", r)
    assert len(calls) == 1
    assert live.blocked_until > time.monotonic()


def test_transient_network_reset_is_retried_without_retrying_http_block(monkeypatch):
    import primp

    calls = []

    def get(*args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            raise primp.RequestError("synthetic connection reset")
        return SimpleNamespace(status_code=200, text=shopping_html(null_results_payload()))

    monkeypatch.setattr(primp, "Client", lambda **kw: SimpleNamespace(get=get))
    monkeypatch.setattr("flightcore.live_quotes.time.sleep", lambda _seconds: None)
    live = LiveQuotes()
    result = live.search("VCE", "ADB", "2026-09-18", request_spec())
    assert result["empty"] is True and len(calls) == 3


def test_live_decoder_worker_continues_after_empty_destinations(tmp_path, monkeypatch):
    r = request_spec(
        start_date="2026-09-18",
        end_date="2026-09-20",
        origins=["VCE", "TSF"],
        destinations=["ADB", "BCN"],
    )
    live, calls = LiveQuotes(), []

    def fetch(query):
        leg = query.pb().data[0]
        origin, destination = leg.from_airport.airport, leg.to_airport.airport
        calls.append((origin, destination))
        if destination == "ADB":
            return shopping_html(
                null_results_payload(origin, destination, suggestions=origin == "VCE")
            )
        row = wire_row()
        segment = row[0][2][0]
        segment[3], segment[6] = origin, destination
        segment[20] = segment[21] = [int(x) for x in leg.date.split("-")]
        return html_payload([row])

    monkeypatch.setattr(live, "_fetch", fetch)
    manager = JobManager(tmp_path / "mixed-search.db", live)
    monkeypatch.setattr(manager, "_launch", lambda job_id: None)
    jid = manager.create(r, build_plan(r, {}))
    manager.run(jid)
    job = manager.public(jid)
    assert job["status"] == "complete" and job["completed"] == job["total"] == 4
    assert job["empty_searches"] == 2 and job["result_count"] == 2
    assert calls[:2] == [("VCE", "ADB"), ("TSF", "ADB")]
    assert all(p["outbound"]["segments"][-1]["to_code"] == "BCN" for p in job["results"])


class FakeLive:
    def __init__(self):
        self.calls = []
        self.block = False
        self.new_price = None
        self.remove = False

    def search(self, origin, destination, day, request, inbound=False, fresh=False):
        self.calls.append((origin, destination, day, inbound, fresh))
        if self.block:
            raise LiveBlocked("Google limitato")
        q = quote(origin, destination, day, price=self.new_price or 30)
        return {"quotes": [] if self.remove else [q], "unverified": 0, "empty": self.remove}


def make_manager(tmp_path, r=None):
    r = r or request_spec()
    live = FakeLive()
    manager = JobManager(tmp_path / "jobs.db", live)
    manager._launch = lambda job_id: None
    job_id = manager.create(r, build_plan(r, {}))
    return manager, live, job_id


def test_job_persists_complete_pairs_and_sums_both_prices(tmp_path):
    m, live, jid = make_manager(tmp_path)
    m.run(jid)
    result = m.public(jid)
    assert result["status"] == "complete" and result["completed"] == 1
    assert result["results"][0]["price"] == 60
    assert len(live.calls) == 2
    reopened = JobManager(tmp_path / "jobs.db", live)
    assert reopened.public(jid)["result_count"] == 1


def test_group_return_tests_both_airports_not_only_departure(tmp_path):
    m, live, jid = make_manager(
        tmp_path, request_spec(origins=["VCE", "TSF"], return_airport="group")
    )
    m.run(jid)
    assert m.public(jid)["result_count"] == 4
    assert {c[1] for c in live.calls if c[3]} == {"VCE", "TSF"}


def test_block_is_partial_resumable_and_never_completed_zero_results(tmp_path):
    m, live, jid = make_manager(tmp_path)
    live.block = True
    m.run(jid)
    assert m.public(jid)["status"] == "blocked"
    assert m.get(jid)["completed"] == 0
    assert len(live.calls) == 1
    live.block = False
    m.resume(jid)
    m.run(jid)
    assert m.public(jid)["status"] == "complete"
    assert m.public(jid)["result_count"] == 1


def test_cancel_stops_before_network_and_resume_continues(tmp_path):
    m, live, jid = make_manager(tmp_path)
    m.cancel(jid)
    m.run(jid)
    assert live.calls == []
    assert m.public(jid)["status"] == "cancelled"
    m.resume(jid)
    m.run(jid)
    assert m.public(jid)["result_count"] == 1


def test_recheck_updates_price_and_removes_no_longer_confirmed_pairs(tmp_path):
    m, live, jid = make_manager(tmp_path)
    m.run(jid)
    pair_id = m.public(jid)["results"][0]["id"]
    live.new_price = 40
    recheck = m.recheck(jid, pair_id)
    assert recheck["price_changed"] and recheck["result"]["price"] == 80
    assert m.public(jid)["results"][0]["price"] == 80
    assert all(c[-1] for c in live.calls[-2:])
    assert m.recheck(jid, pair_id)["price_changed"] is False
    live.remove = True
    assert m.recheck(jid, pair_id)["status"] == "not_confirmed"
    assert m.public(jid)["result_count"] == 0


def test_old_prices_are_explicitly_marked_for_recheck(tmp_path):
    m, _live, jid = make_manager(tmp_path)
    m.run(jid)
    job = m.get(jid)
    for pair in job["results"].values():
        pair["checked_at"] = "2020-01-01T00:00:00+00:00"
    m.save(job)
    assert m.public(jid)["results"][0]["needs_recheck"] is True


def test_live_adapter_caches_identical_searches_but_fresh_bypasses(tmp_path, monkeypatch):
    live = LiveQuotes()
    calls = []

    def fetch(query):
        calls.append(query.params())
        return html_payload([wire_row()], None)

    monkeypatch.setattr(live, "_fetch", fetch)
    r = request_spec(baggage="cabin")
    first = live.search("VCE", "BCN", "2026-10-02", r)
    live.search("VCE", "BCN", "2026-10-02", r)
    assert len(calls) == 1
    assert first["quotes"][0]["baggage"] == "cabin"
    live.search("VCE", "BCN", "2026-10-02", r, fresh=True)
    assert len(calls) == 2


def test_flexible_api_invalid_payload_and_page_without_network(monkeypatch, tmp_path):
    import flightcore.flexible_api as endpoints
    from app import app

    m, _live, _jid = make_manager(tmp_path)
    monkeypatch.setattr(endpoints, "manager", m)
    client = app.test_client()
    for body in ([], None, {"start_date": "bad"}):
        assert client.post("/api/flexible/plan", json=body).status_code == 400
    page = client.get("/")
    assert page.status_code == 200
    assert b"Weekend Finder" in page.data and b"Flexible Trip Finder" in page.data
    assert b"cdn.tailwindcss" not in page.data
    plan = client.post("/api/flexible/plan", json=request_spec().to_dict())
    assert plan.status_code == 200 and plan.json["plan"]["searches_upper_bound"] == 2
    assert client.get("/api/flexible/jobs/missing").status_code == 404


def test_large_plan_is_rejected_before_materializing_millions_of_tasks():
    r = request_spec(
        origins=["VCE", "TSF", "BLQ", "BGY", "VRN"],
        destinations=[],
        end_date="2027-09-15",
        departure_days=list(range(7)),
        return_days=list(range(7)),
        min_nights=1,
        max_nights=30,
    )
    with pytest.raises(SearchValidationError, match="Riduci"):
        build_plan(r, {"VCE": ["BCN", "LHR", "MAD", "LIS", "TLS"]})


def test_live_airline_name_overrides_reassigned_iata_enum():
    row = wire_row()
    row[0][0], row[0][1], row[0][2][0][22] = "W4", ["Wizz Air Malta"], ["W4", "6728"]
    quotes, rejected = decode(
        html_payload([row], airlines=[["W4", "Wizz Air Malta"]]), request_spec(), ""
    )
    assert rejected == 0
    assert quotes[0]["segments"][0]["airline_name"] == "Wizz Air Malta"


@pytest.mark.parametrize("end,expected", [("19:59", 20), ("19:00", 19), ("23:59", None)])
def test_google_hour_query_does_not_drop_last_partial_hour(monkeypatch, end, expected):
    captured = []
    live = LiveQuotes()

    def fetch(query):
        captured.append(query.pb())
        return html_payload()

    monkeypatch.setattr(live, "_fetch", fetch)
    live.search("VCE", "BCN", "2026-10-02", request_spec(departure_max=end))
    leg = captured[0].data[0]
    upper = leg.latest_departure_hour if leg.HasField("latest_departure_hour") else None
    assert upper == expected


def test_google_request_interval_is_configurable_with_safe_bounds(monkeypatch):
    from flightcore.live_quotes import request_interval

    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "0.2")
    assert request_interval() == 1.0
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "3.25")
    assert request_interval() == 3.25
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "99")
    assert request_interval() == 10.0
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "not-a-number")
    assert request_interval() == 1.5


def test_consent_is_scoped_to_google_fetcher_and_refreshes(monkeypatch):
    import primp
    from fast_flights import fetcher

    from flightcore.consent import _install_into_primp

    seen = []

    def client(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(primp, "Client", client)
    monkeypatch.setattr(fetcher, "Client", client)
    _install_into_primp({"SOCS": "first-synthetic"})
    fetcher.Client()
    _install_into_primp({"SOCS": "second-synthetic"})
    fetcher.Client()
    assert primp.Client is client
    assert [c["cookies"]["SOCS"] for c in seen] == ["first-synthetic", "second-synthetic"]


def test_real_worker_cancellation_and_single_active_job(tmp_path):
    entered, release = Event(), Event()
    live = FakeLive()
    original = live.search

    def search(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    live.search = search
    m = JobManager(tmp_path / "jobs.db", live)
    r = request_spec()
    jid = m.create(r, build_plan(r, {}))
    try:
        assert entered.wait(5)
        assert m.public(jid)["status"] == "running"
        with pytest.raises(ValueError, match="già in corso"):
            m.create(r, build_plan(r, {}))
        m.cancel(jid)
    finally:
        release.set()
        m._worker.join(5)
    assert not m._worker.is_alive()
    assert m.public(jid)["status"] == "cancelled"
    assert m.get(jid)["completed"] == 0 and len(live.calls) == 1
    m.resume(jid)
    m._worker.join(5)
    assert m.public(jid)["status"] == "complete"


def test_failed_recheck_is_503_without_false_confirmation(monkeypatch, tmp_path):
    import flightcore.flexible_api as endpoints
    from app import app

    m, live, jid = make_manager(tmp_path)
    m.run(jid)
    pair_id = m.public(jid)["results"][0]["id"]
    monkeypatch.setattr(endpoints, "manager", m)
    live.block = True
    response = app.test_client().post(f"/api/flexible/jobs/{jid}/results/{pair_id}/recheck")
    assert response.status_code == 503 and response.json["status"] == "unavailable"
    assert m.get(jid)["rechecks"] == {}
