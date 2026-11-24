"""Contract tests: exact weekends, complete quotes, UTC elapsed time, saved job lifecycle.

Synthetic Google rows below contain no real tokens, cookies or network calls.
"""

import base64
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest

from flightcore.flexible import (
    GOOGLE_HORIZON_DAYS,
    FlexibleRequest,
    Schedule,
    SearchValidationError,
    build_plan,
    combine,
    fingerprint,
    local_instant,
    matches,
    pair_batches,
)
from flightcore.flexible_jobs import JobManager
from flightcore.live_quotes import (
    AirportGroupQuery,
    LiveBlocked,
    LiveQuotes,
    LiveTransient,
    LiveUnavailable,
    decode,
)
from flightcore.result_browser import ResultFilters


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


def test_only_connections_google_marks_as_self_transfer_need_opting_in():
    q = quote(destination="MAD", arrival="19:30")
    q["segments"].extend(quote(origin="MAD", departure="20:30", arrival="21:30")["segments"])
    r = request_spec(max_stops=1)
    assert q["self_transfer"] is None  # how Google sends an ordinary single ticket
    assert matches(q, r, "VCE", "BCN", "2026-10-02")
    q["self_transfer"] = False
    assert matches(q, r, "VCE", "BCN", "2026-10-02")
    q["self_transfer"] = True
    assert not matches(q, r, "VCE", "BCN", "2026-10-02")
    assert matches(q, replace(r, allow_self_transfer=True), "VCE", "BCN", "2026-10-02")


def test_low_cost_filter_checks_each_segment():
    q = quote()
    q["segments"][0]["airline_code"] = "BA"
    assert not matches(q, request_spec(airline_mode="low_cost"), "VCE", "BCN", "2026-10-02")


def test_plan_does_not_treat_missing_route_as_proof_of_no_service():
    r = request_spec(origins=["VCE", "TSF"], destinations=[], return_airport="group")
    p = build_plan(r, {"VCE": ["BCN"], "TSF": ["LHR"]})
    assert p["combinations"] == 4
    assert set(Schedule(r, p["destinations"]).routes) == {
        ("VCE", "BCN"),
        ("TSF", "BCN"),
        ("VCE", "LHR"),
        ("TSF", "LHR"),
    }
    assert p["distinct_searches"] == 8
    assert p["searches_upper_bound"] == 2  # one request for the outbounds, one for the returns
    assert p["candidate_count"] == 2


def test_connections_are_planned_one_route_per_request():
    r = request_spec(
        origins=["VCE", "TSF"], destinations=["BCN", "LHR"], max_stops=1, allow_self_transfer=True
    )
    p = build_plan(r, {})
    assert p["airports_per_search"] == 1
    assert p["searches_upper_bound"] == p["distinct_searches"] == 8


@pytest.mark.parametrize("size", [1, 2, 3, 10])
def test_batches_cover_exactly_the_needed_pairs(size):
    origins, destinations = ["VCE", "TSF", "VRN"], ["BCN", "LHR", "TSF", "MAD", "VCE"]
    pairs = [(o, d) for d in destinations for o in origins if o != d]
    pairs.remove(("VRN", "MAD"))
    batches = pair_batches(pairs, size)
    covered = [(a, b) for sources, targets in batches for a in sources for b in targets]
    assert sorted(covered) == sorted(pairs)  # nothing missing, extra or repeated
    assert all(len(side) <= size for batch in batches for side in batch)


def test_ten_airports_per_side_fill_one_request():
    destinations = [f"D{i:02d}" for i in range(25)]
    batches = pair_batches([(o, d) for d in destinations for o in ("VCE", "TSF")], 10)
    assert [len(targets) for _sources, targets in batches] == [10, 10, 5]
    assert all(sources == ("VCE", "TSF") for sources, _targets in batches)


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


GOOGLE_ERROR_PAGE = (
    "<script class=\"ds:1\">AF_initDataCallback({key: 'ds:1',  data:[13,null,"
    '[["type.googleapis.com/travel.frontend.flights.ErrorResponse",[[null,[[1,2,3],'
    'null,null,null,null,[[0]]],0,"a","b"],0]]]],errorHasStatus: true,});</script>'
)


def test_google_error_response_is_transient_and_retried(monkeypatch):
    with pytest.raises(LiveTransient):
        decode(GOOGLE_ERROR_PAGE, request_spec(), "")
    monkeypatch.setattr("flightcore.live_quotes.time.sleep", lambda _seconds: None)
    live = LiveQuotes()
    pages = [GOOGLE_ERROR_PAGE, html_payload([wire_row()])]
    monkeypatch.setattr(live, "_fetch", lambda _query: pages.pop(0))
    found = live.search("VCE", "BCN", "2026-10-02", request_spec())
    assert len(found["quotes"]) == 1 and not pages
    live = LiveQuotes()
    monkeypatch.setattr(live, "_fetch", lambda _query: GOOGLE_ERROR_PAGE)
    with pytest.raises(LiveTransient):
        live.search("VCE", "BCN", "2026-10-02", request_spec())


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


def query_airports(query):
    leg = query.pb().data[0]
    return (
        getattr(query, "origins", (leg.from_airport.airport,)),
        getattr(query, "destinations", (leg.to_airport.airport,)),
        leg.date,
    )


def row_for(origin, destination, day, price=30):
    row = wire_row(price)
    segment = row[0][2][0]
    segment[3], segment[6] = origin, destination
    segment[20] = segment[21] = [int(x) for x in day.split("-")]
    return row


def test_live_decoder_worker_continues_after_empty_destinations(tmp_path, monkeypatch):
    r = request_spec(
        start_date="2026-09-18",
        end_date="2026-09-20",
        origins=["VCE", "TSF"],
        destinations=["ADB", "BCN"],
    )
    live, calls = LiveQuotes(), []

    def fetch(query):
        origins, destinations, day = query_airports(query)
        calls.append((origins, destinations))
        served = {"BCN", "VCE", "TSF"}  # Izmir has no flights at all
        return html_payload(
            [row_for(o, d, day) for o in origins for d in destinations if {o, d} <= served]
        )

    monkeypatch.setattr(live, "_fetch", fetch)
    manager = JobManager(tmp_path / "mixed-search.db", live)
    monkeypatch.setattr(manager, "_launch", lambda job_id: None)
    jid = manager.create(r, build_plan(r, {}))
    manager.run(jid)
    job = manager.public(jid)
    assert job["status"] == "complete" and job["completed"] == job["total"] == 4
    assert job["empty_searches"] == 2 and job["result_count"] == 2
    # Both origins and both destinations share one request; only Barcelona,
    # where an outbound was found, is asked for a return.
    # Then the cheapest trip of each of the two routes is compared with a return ticket.
    assert calls[:2] == [(("VCE", "TSF"), ("ADB", "BCN")), (("BCN",), ("VCE", "TSF"))]
    assert len(calls) == 4
    assert all(p["outbound"]["segments"][-1]["to_code"] == "BCN" for p in job["results"])


def test_returns_share_one_request_but_only_wanted_routes_become_trips(tmp_path, monkeypatch):
    r = request_spec(origins=["VCE", "TSF"], destinations=["BCN", "MAD"])
    live, calls = LiveQuotes(), []
    outbound = {("VCE", "BCN"), ("TSF", "MAD")}

    def fetch(query):
        origins, destinations, day = query_airports(query)
        calls.append((origins, destinations))
        pairs = [(o, d) for o in origins for d in destinations]
        return html_payload(
            [row_for(o, d, day) for o, d in pairs if day.endswith("04") or (o, d) in outbound]
        )

    monkeypatch.setattr(live, "_fetch", fetch)
    manager = JobManager(tmp_path / "returns.db", live)
    monkeypatch.setattr(manager, "_launch", lambda job_id: None)
    plan = build_plan(r, {})
    jid = manager.create(r, plan)
    manager.run(jid)
    assert calls[:2] == [(("VCE", "TSF"), ("BCN", "MAD")), (("BCN", "MAD"), ("VCE", "TSF"))]
    assert plan["searches_upper_bound"] == 2 and len(calls) == 2 + 2  # plus two comparisons
    trips = manager.get(jid)["results"].values()
    # Barcelona-Treviso was priced with the group, but nobody flew out of Treviso to Barcelona.
    assert sorted(
        (t["outbound"]["segments"][0]["from_code"], t["inbound"]["segments"][-1]["to_code"])
        for t in trips
    ) == [("TSF", "TSF"), ("VCE", "VCE")]


def test_group_search_splits_offers_by_route_and_caches_each(monkeypatch):
    live, calls = LiveQuotes(), []

    def fetch(query):
        origins, destinations, day = query_airports(query)
        calls.append((origins, destinations))
        rows = [row_for("VCE", "BCN", day, 30), row_for("TSF", "BCN", day, 40)]
        return html_payload([*rows, row_for("VCE", "PMI", day, 50)])  # Palma was not asked

    monkeypatch.setattr(live, "_fetch", fetch)
    r = request_spec()
    found = live.search_many(("VCE", "TSF"), ("BCN", "MAD"), "2026-10-02", r)
    assert calls == [(("VCE", "TSF"), ("BCN", "MAD"))]
    assert {pair: [q["price"] for q in x["quotes"]] for pair, x in found.items()} == {
        ("VCE", "BCN"): [30.0],
        ("VCE", "MAD"): [],
        ("TSF", "BCN"): [40.0],
        ("TSF", "MAD"): [],
    }
    assert sum(x["unverified"] for x in found.values()) == 1
    assert not any(x["empty"] for x in found.values())  # an unplaced offer rules out "empty"
    assert live.search("TSF", "BCN", "2026-10-02", r)["quotes"][0]["price"] == 40
    assert len(calls) == 1  # each route of the group is cached as its own search
    live.search("TSF", "BCN", "2026-10-02", r, fresh=True)
    assert calls[-1] == (("TSF",), ("BCN",))


def test_connections_are_never_searched_as_a_group(monkeypatch):
    live, calls = LiveQuotes(), []

    def fetch(query):
        origins, destinations, day = query_airports(query)
        calls.append((origins, destinations))
        return html_payload([row_for(origins[0], destinations[0], day)])

    monkeypatch.setattr(live, "_fetch", fetch)
    found = live.search_many(
        ("VCE", "TSF"),
        ("BCN", "MAD"),
        "2026-10-02",
        request_spec(max_stops=1, allow_self_transfer=True),
    )
    assert len(found) == len(calls) == 4
    assert all(len(o) == len(d) == 1 for o, d in calls)


def test_group_query_lists_every_airport_and_matches_fast_flights_for_one_route():
    live, queries = LiveQuotes(), []
    live._fetch = lambda query: queries.append(query) or html_payload([wire_row()])
    live.search_many(("VCE",), ("BCN",), "2026-10-02", request_spec())
    live.search_many(("VCE", "TSF"), ("BCN", "MAD", "LIS"), "2026-10-03", request_spec())
    single, group = queries
    assert AirportGroupQuery(single, ("VCE",), ("BCN",)).params() == single.params()
    raw = base64.b64decode(group.params()["tfs"])
    airports = [
        raw[i + 4 : i + 7].decode()
        for i in range(len(raw))
        if raw[i + 1 : i + 4] == b"\x05\x12\x03"
    ]
    assert airports == ["VCE", "TSF", "BCN", "MAD", "LIS"]
    assert group.params()["curr"] == "EUR" and group.params()["tfs"] in group.url()


def test_empty_group_response_must_name_exactly_the_requested_airports():
    payload = null_results_payload()
    payload[1] = [
        [
            [[["VCE", 0], "Venezia"], [["TSF", 0], "Treviso"]],
            [[["ADB", 0], "Smirne"], [["KIV", 0], "Chisinau"]],
        ]
    ]
    html = shopping_html(payload)
    assert decode(html, request_spec(), "", expected_route=(("TSF", "VCE"), ("KIV", "ADB"))) == (
        [],
        0,
    )
    for other in ((("VCE",), ("ADB", "KIV")), (("VCE", "TSF"), ("ADB", "KIV", "BCN"))):
        with pytest.raises(LiveUnavailable):
            decode(html, request_spec(), "", expected_route=other)


def test_group_google_cannot_answer_is_retried_route_by_route(monkeypatch):
    live, calls = LiveQuotes(), []
    monkeypatch.setattr("flightcore.live_quotes.time.sleep", lambda _seconds: None)

    def fetch(query):
        origins, destinations, day = query_airports(query)
        calls.append((origins, destinations))
        if len(destinations) > 1:
            return GOOGLE_ERROR_PAGE
        return html_payload([row_for(origins[0], destinations[0], day)])

    monkeypatch.setattr(live, "_fetch", fetch)
    found = live.search_many(("VCE",), ("BCN", "MAD"), "2026-10-02", request_spec())
    assert [len(x["quotes"]) for x in found.values()] == [1, 1]
    assert calls[-2:] == [(("VCE",), ("BCN",)), (("VCE",), ("MAD",))]


def test_a_block_during_a_group_search_is_never_retried(monkeypatch):
    live, calls = LiveQuotes(), []

    def fetch(query):
        calls.append(query)
        raise LiveBlocked("Google ha limitato le richieste.")

    monkeypatch.setattr(live, "_fetch", fetch)
    with pytest.raises(LiveBlocked):
        live.search_many(("VCE", "TSF"), ("BCN", "MAD"), "2026-10-02", request_spec())
    assert len(calls) == 1


class FakeLive:
    def __init__(self):
        self.calls = []
        self.block = False
        self.new_price = None
        self.remove = False
        self.round_trip_calls = []
        self.round_trip_fares = {}

    def search(self, origin, destination, day, request, inbound=False, fresh=False):
        self.calls.append((origin, destination, day, inbound, fresh))
        if self.block:
            raise LiveBlocked("Google limitato")
        q = quote(origin, destination, day, price=self.new_price or 30)
        return {"quotes": [] if self.remove else [q], "unverified": 0, "empty": self.remove}

    def round_trip(self, origin, destination, out, back, request):
        self.round_trip_calls.append((origin, destination, out, back))
        return self.round_trip_fares

    def search_many(self, origins, destinations, day, request, inbound=False, fresh=False):
        return {
            (o, d): self.search(o, d, day, request, inbound, fresh)
            for o in origins
            for d in destinations
            if o != d
        }


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
    assert result["status"] == "complete" and result["completed"] == result["total"] == 3
    assert live.round_trip_calls == [("VCE", "BCN", "2026-10-02", "2026-10-04")]
    assert result["eta_seconds"] == 0 and "outbound" not in result
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


def test_each_leg_is_priced_once_across_overlapping_stays(tmp_path):
    r = request_spec(
        end_date="2026-10-08",
        origins=["VCE", "TSF"],
        destinations=["BCN", "MAD"],
        departure_days=list(range(7)),
        return_days=list(range(7)),
        min_nights=2,
        max_nights=4,
    )
    m, live, jid = make_manager(tmp_path, r)
    m.run(jid)
    job = m.public(jid)
    legs = [call[:4] for call in live.calls]
    assert len(legs) == len(set(legs))  # 2, 3 and 4 nights share the same flights
    assert {c[2] for c in live.calls if not c[3]} == {f"2026-10-0{d}" for d in range(1, 7)}
    assert {c[2] for c in live.calls if c[3]} == {f"2026-10-0{d}" for d in range(3, 9)}
    # 15 date pairs x 2 destinations x 2 origins, each with one flight per leg.
    assert job["status"] == "complete" and job["result_count"] == 60
    assert job["completed"] == job["total"] == 12 + 60 and m.get(jid)["outbound"] == {}
    # One comparison per route and pair of dates: each route's cheapest dates first.
    assert len(set(live.round_trip_calls)) == 60
    assert {call[:2] for call in live.round_trip_calls[:4]} == {
        (o, d) for o in ("VCE", "TSF") for d in ("BCN", "MAD")
    }
    assert sorted({trip["nights"] for trip in m.get(jid)["results"].values()}) == [2, 3, 4]


def test_wide_search_with_connections_prices_direct_flights_first(tmp_path):
    destinations = ["BCN", "MAD", "LIS", "OPO", "AGP", "VLC"]
    r = request_spec(origins=["VCE", "TSF"], destinations=destinations, max_stops=2)
    plan = build_plan(r, {})
    assert plan["direct_first"] and plan["searches_upper_bound"] == 2 + 24
    m, live, jid = make_manager(tmp_path, r)
    seen = []
    original = live.search_many

    def search_many(origins, targets, day, request, inbound=False, fresh=False):
        seen.append((len(origins), len(targets), request.max_stops))
        return original(origins, targets, day, request, inbound, fresh)

    live.search_many = search_many
    m.run(jid)
    # Two grouped non-stop searches, then each route on its own with connections.
    assert seen[:2] == [(2, 6, 0), (6, 2, 0)] and set(seen[2:]) == {(1, 1, 2)}
    job = m.public(jid)
    assert job["status"] == "complete" and job["completed"] == job["total"] == 26 + 12
    assert job["result_count"] == 12


def test_few_routes_with_connections_need_no_direct_pass():
    r = request_spec(origins=["VCE", "TSF"], destinations=["LAX"], max_stops=3)
    plan = build_plan(r, {})
    assert not plan["direct_first"] and plan["airports_per_search"] == 1
    assert plan["searches_upper_bound"] == 4


def test_cheaper_single_return_ticket_is_shown_next_to_the_two_one_ways(tmp_path):
    m, live, jid = make_manager(tmp_path)
    outbound_id = fingerprint(quote())
    live.round_trip_fares = {outbound_id: 45.0, "another-outbound": 20.0}
    m.run(jid)
    job = m.public(jid)
    trip = job["results"][0]
    # A hint only: its return may be another one, the trip keeps its own price.
    assert (trip["price"], trip["round_trip_from"], trip["ticketing"]) == (
        60,
        45.0,
        "separate_one_way",
    )
    assert job["calendar"][0]["price"] == 60 and "round_trips" not in job
    assert m.public(jid, filters=ResultFilters.parse({"max_price": "50"}))["filtered_count"] == 0
    assert m.export(jid)[1][0]["price"] == 60
    assert m.recheck(jid, trip["id"])["result"]["price"] == 60  # the two tickets, live
    saved = m.get(jid)
    saved["round_trips"]["VCE|BCN|2026-10-02|2026-10-04"][outbound_id] = 60.0
    m.save(saved)
    trip = m.public(jid)["results"][0]  # not cheaper: no hint
    assert trip["price"] == 60 and "round_trip_from" not in trip


def test_unreadable_round_trip_page_never_fails_a_finished_search(tmp_path):
    m, live, jid = make_manager(tmp_path)
    live.round_trip_fares = None
    m.run(jid)
    job = m.public(jid)
    assert job["status"] == "complete" and job["results"][0]["price"] == 60


def test_one_way_and_open_jaw_trips_are_not_compared_with_a_return_ticket(tmp_path):
    m, live, jid = make_manager(tmp_path, request_spec(trip_type="one_way"))
    m.run(jid)
    assert live.round_trip_calls == [] and m.public(jid)["status"] == "complete"
    trip = {"outbound": quote(), "inbound": quote("MAD", "VCE", "2026-10-04")}
    assert JobManager._round_trip_key(trip) is None


def test_round_trip_page_gives_the_from_price_of_each_outbound(monkeypatch):
    live, seen = LiveQuotes(), []

    def fetch(query):
        seen.append(query.pb())
        rows = [row_for("VCE", "BCN", "2026-10-02", 80), row_for("VCE", "MAD", "2026-10-02", 10)]
        return html_payload(rows)

    monkeypatch.setattr(live, "_fetch", fetch)
    r = request_spec(return_min="06:00")
    fares = live.round_trip("VCE", "BCN", "2026-10-02", "2026-10-04", r)
    assert list(fares.values()) == [80.0]  # the Madrid row is not this route
    legs = seen[0].data
    assert [(x.from_airport.airport, x.to_airport.airport, x.date) for x in legs] == [
        ("VCE", "BCN", "2026-10-02"),
        ("BCN", "VCE", "2026-10-04"),
    ]
    assert (legs[0].earliest_departure_hour, legs[1].earliest_departure_hour) == (18, 6)
    monkeypatch.setattr(live, "_fetch", lambda query: "<html>captcha</html>")
    assert live.round_trip("VCE", "BCN", "2026-10-02", "2026-10-04", r) is None


def test_dates_google_does_not_sell_yet_are_left_out_and_reported(tmp_path):
    r = request_spec(
        start_date="2026-10-01",
        end_date="2026-10-11",
        departure_days=list(range(7)),
        return_days=list(range(7)),
    )
    today = date(2026, 10, 6) - timedelta(days=GOOGLE_HORIZON_DAYS)  # sold up to 6 October
    schedule = Schedule(r, ["BCN"], today)
    assert schedule.days[-1] == "2026-10-06" and schedule.date_pairs[-1][1] == "2026-10-06"
    assert schedule.unsold_from == "2026-10-07"
    assert Schedule(r, ["BCN"], date(2026, 9, 15)).unsold_from is None
    m, live, jid = make_manager(tmp_path, r)
    m.run(jid)
    job = m.public(jid)
    assert job["status"] == "complete" and job["issues"] == []  # all on sale today
    beyond = request_spec(start_date="2030-01-04", end_date="2030-01-06")
    job = m.get(jid)
    job.update(request=beyond.to_dict(), status="interrupted", cursor=[0, 0, 0], completed=0)
    job["pass"] = 0
    m.save(job)
    calls = len(live.calls)
    m.run(jid)
    job = m.public(jid)
    assert job["status"] == "complete" and "Google vende i voli fino al" in job["issues"][0]
    assert len(live.calls) == calls  # nothing is asked for dates Google would refuse


def test_no_return_is_priced_without_an_outbound_within_budget(tmp_path):
    m, live, jid = make_manager(tmp_path, request_spec(budget=20))
    m.run(jid)
    job = m.public(jid)
    assert job["status"] == "complete" and job["result_count"] == 0
    assert job["completed"] == job["total"] == 2
    assert [call[3] for call in live.calls] == [False]


def test_resume_after_a_block_on_the_return_keeps_the_priced_outbound(tmp_path):
    m, live, jid = make_manager(tmp_path)
    original = live.search

    def search(origin, destination, day, request, inbound=False, fresh=False):
        live.block = inbound
        return original(origin, destination, day, request, inbound, fresh)

    live.search = search
    m.run(jid)
    saved = m.get(jid)
    assert saved["status"] == "blocked" and saved["cursor"] == [1, 0, 0]
    assert list(saved["outbound"]) == ["VCE|BCN|2026-10-02"]
    live.search = original
    live.block = False
    m.resume(jid)
    m.run(jid)
    assert m.public(jid)["status"] == "complete" and m.public(jid)["result_count"] == 1
    assert [call[3] for call in live.calls] == [False, True, True]


def test_job_saved_by_the_previous_engine_searches_again_and_keeps_results(tmp_path):
    m, live, jid = make_manager(tmp_path)
    m.run(jid)
    job = m.get(jid)
    for key in ("engine", "cursor", "outbound", "requests", "return_days", "return_requests"):
        job.pop(key)
    job.update(status="interrupted", completed=1, total=1)
    job["plan"]["tasks"] = [["2026-10-02", "2026-10-04", "BCN", "VCE"]]
    m.save(job)
    m.resume(jid)
    m.run(jid)
    result = m.public(jid)
    assert result["status"] == "complete" and result["completed"] == result["total"] == 2
    assert result["result_count"] == 1 and len(live.calls) == 4 and "tasks" not in result["plan"]


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


def test_flexible_api_invalid_payload_and_page_without_network(monkeypatch, tmp_path, frozen_today):
    import flightcore.flexible_api as endpoints
    from app import app

    m, _live, _jid = make_manager(tmp_path)
    monkeypatch.setattr(endpoints, "manager", m)
    client = app.test_client()
    for body in ([], None, {"start_date": "bad"}):
        assert client.post("/api/flexible/plan", json=body).status_code == 400
    page = client.get("/")
    assert page.status_code == 200
    assert b"trip-type" in page.data and b"date-mode" in page.data
    assert b"Ricerca classica" not in page.data
    assert client.get("/classic").headers["Location"] == "/"
    assert client.get("/weekends").headers["Location"] == "/"
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

    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "0.1")
    assert request_interval() == 0.3
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "3.25")
    assert request_interval() == 3.25
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "99")
    assert request_interval() == 10.0
    monkeypatch.setenv("FLIGHT_FINDER_GOOGLE_INTERVAL", "not-a-number")
    assert request_interval() == 0.3


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
    # The outbound in flight was priced; the return was never requested.
    assert m.get(jid)["completed"] == 1 and len(live.calls) == 1
    m.resume(jid)
    m._worker.join(5)
    assert m.public(jid)["status"] == "complete"
    assert [call[3] for call in live.calls] == [False, True]  # outbound not priced twice


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


def test_round_trip_link_opens_one_google_search_for_both_dates():
    from flightcore.live_quotes import round_trip_url

    r = request_spec(adults=2)
    url = round_trip_url(combine(quote(), returning(), r), r)
    assert url.startswith("https://www.google.com/travel/flights")
    assert round_trip_url({"inbound": None}, r) is None
