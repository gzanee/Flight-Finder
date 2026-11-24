"""Resumable local search jobs. Persist progress; stop on blocks, never invent no availability."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .airports import details as airport_details
from .flexible import (
    FlexibleRequest,
    combine,
    fingerprint,
    matches,
    return_routes,
    search_passes,
    single_trip,
)
from .live_quotes import (
    QUOTE_TTL,
    LiveBlocked,
    LiveQuotes,
    LiveUnavailable,
    round_trip_url,
    seconds_per_search,
)
from .result_browser import ResultFilters, browse_results, sort_key

# Jobs saved by an engine with another cursor search again, keeping their trips.
ENGINE = 3
# A job with thousands of trips is several MB: write it at most this often.
# The last second of work is repeated after a crash, never lost or invented.
SAVE_INTERVAL = 1.0
# Requests of this run to observe before trusting their measured pace.
PACE_SAMPLE = 3
# After the search, trips are compared with Google's single-ticket return
# fares: one request per route and pair of dates, at most this many.
ROUND_TRIP_CHECKS = 150
SHOWN_AIRPORTS = 3


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def airport_list(codes):
    if len(codes) <= SHOWN_AIRPORTS:
        return ", ".join(codes)
    return f"{', '.join(codes[: SHOWN_AIRPORTS - 1])} e altri {len(codes) - SHOWN_AIRPORTS + 1}"


def result_airports(job):
    codes = set(job["request"]["origins"])
    for trip in job["results"]:
        for leg in (trip["outbound"], trip["inbound"]):
            if leg:
                for segment in leg["segments"]:
                    codes.update((segment["from_code"], segment["to_code"]))
    return {code: airport_details(code) for code in sorted(codes)}


class JobManager:
    def __init__(self, path=None, live=None):
        self.path = Path(
            path or Path(os.getenv("FLIGHT_FINDER_CACHE_DIR", ".cache")) / "weekends.sqlite3"
        )
        self.live = live or LiveQuotes()
        self._lock = threading.Lock()
        self._worker = None
        self._active_job_id = None
        self._ready = False
        self._saved_at = 0.0
        self._save_cost = 0.0

    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        if not self._ready:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            db.commit()
            self._ready = True
        return db

    def save(self, job):
        db = self.connection()
        try:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))
            db.commit()
        finally:
            db.close()

    def stored(self, job_id, *fields):
        """Single fields of a saved job, without decoding its results."""
        db = self.connection()
        try:
            columns = ", ".join("json_extract(body, ?)" for _ in fields)
            row = db.execute(
                f"SELECT {columns} FROM jobs WHERE id = ?",
                (*("$." + field for field in fields), job_id),
            ).fetchone()
        finally:
            db.close()
        if not row:
            raise KeyError(job_id)
        return row

    def get(self, job_id):
        db = self.connection()
        try:
            row = db.execute("SELECT body FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            db.close()
        if not row:
            raise KeyError(job_id)
        return json.loads(row[0])

    def create(self, request, plan):
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise ValueError("Una ricerca è già in corso: fermala prima di avviarne un altra.")
            job = {
                "id": uuid.uuid4().hex,
                "request": asdict(request),
                "plan": plan,
                "status": "running",
                "engine": ENGINE,
                "completed": 0,
                "total": plan["searches_upper_bound"],
                "cursor": [0, 0, 0],
                "pass": 0,
                "outbound": {},
                "requests": 0,
                "return_days": 0,
                "return_requests": 0,
                "eta_seconds": None,
                "results": {},
                "rechecks": {},
                "unverified": 0,
                "empty_searches": 0,
                "issues": [],
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "current": None,
            }
            self.save(job)
            self._launch(job["id"])
        return job["id"]

    def _launch(self, job_id):
        self._active_job_id = job_id
        self._worker = threading.Thread(target=self.run, args=(job_id,), daemon=True)
        self._worker.start()

    def resume(self, job_id):
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise ValueError("Attendi che la ricerca attiva si fermi.")
            job = self.get(job_id)
            if job["status"] == "complete":
                raise ValueError("Ricerca già completata. Avviane una nuova per prezzi aggiornati.")
            job["status"] = "running"
            job["issues"] = []
            self.save(job)
            self._launch(job_id)

    def cancel(self, job_id):
        with self._lock:
            job = self.get(job_id)
            if job["status"] == "running":
                job["status"] = "cancelling"
                self.save(job)

    def stopped(self, job_id):
        return self.stored(job_id, "status")[0] == "cancelling"

    def run(self, job_id):
        job = self.get(job_id)
        request = FlexibleRequest(**job["request"])
        passes = search_passes(request, job["plan"]["destinations"])
        if job.get("engine") != ENGINE:
            job.update(engine=ENGINE, completed=0, cursor=[0, 0, 0], outbound={}, requests=0)
            job.update({"pass": 0, "return_days": 0, "return_requests": 0})
        job["total"] = sum(schedule.total for schedule in passes)
        self._saved_at = 0.0
        self._save_cost = 0.0
        pace = {"started": time.monotonic(), "requests": 0}
        try:
            status = "complete"
            for number in range(job["pass"], len(passes)):
                pace["later"] = sum(
                    p.total * seconds_per_search(p.request) for p in passes[number + 1 :]
                )
                pace["done"] = sum(p.total for p in passes[:number])
                pace["label"] = (
                    ("Voli diretti · ", "Con scalo · ")[number] if len(passes) > 1 else ""
                )
                if self._search(job, request, passes[number], pace) == "cancelled":
                    status = "cancelled"
                    break
                job.update({"pass": number + 1, "cursor": [0, 0, 0], "outbound": {}})
                job.update(return_days=0, return_requests=0)
            if status == "complete" and not self._compare_round_trips(job, request):
                status = "cancelled"
            job["status"] = status
            if status == "complete" and passes[-1].unsold_from:
                job["issues"] = [
                    f"Google vende i voli fino al {'/'.join(passes[-1].horizon.split('-')[::-1])}: "
                    "le date successive non sono state cercate."
                ]
        except LiveBlocked as exc:
            job["status"] = "blocked"
            job["issues"] = [str(exc)]
        except Exception as exc:
            job["status"] = "interrupted"
            job["issues"] = [
                str(exc)
                if isinstance(exc, LiveUnavailable)
                else "Ricerca interrotta da un errore; il progresso è salvato."
            ]
        finally:
            job["updated_at"] = utc_now()
            if job["status"] == "complete":
                job["eta_seconds"] = 0
            with self._lock:
                if self.stored(job_id, "status")[0] == "cancelling":
                    job["status"] = "cancelled"
                job["rechecks"] = json.loads(self.stored(job_id, "rechecks")[0] or "{}")
                self.save(job)

    def _search(self, job, request, schedule, pace):
        """Run one schedule from the saved cursor; return the final status.

        The cursor is (day, stage, batch): stage 0 prices the outbound legs of
        the day, stage 1 the returns its earlier outbounds made worth pricing.
        A stopped batch is not counted, so it is searched again on resume.
        Searches use the schedule's request; offers are judged by the user's.
        """
        start, stage, batch = job["cursor"]
        searched, done, prefix = schedule.request, pace["done"], pace["label"]
        for index in range(start, len(schedule.days)):
            day = schedule.days[index]
            base = done + schedule.units_before(index)
            outs = len(schedule.out_batches) if day in schedule.out_days else 0
            if stage == 0:
                for number in range(batch, outs):
                    origins, destinations = schedule.out_batches[number]
                    label = f"{airport_list(origins)} → {airport_list(destinations)} · {day}"
                    if not self._begin(job, prefix + label):
                        return "cancelled"
                    found = self.live.search_many(origins, destinations, day, searched)
                    self._keep_outbound(job, request, day, found)
                    job["cursor"], job["completed"] = [index, 0, number + 1], base + number + 1
                    self._advance(job, schedule, pace)
                stage, batch = 1, 0
            if day in schedule.outs_for:
                wanted = self._wanted_returns(job, request, schedule, day)
                batches = schedule.return_batches(wanted)
                for number in range(batch, len(batches)):
                    aways, homes = batches[number]
                    label = f"Ritorno: {airport_list(aways)} → {airport_list(homes)} · {day}"
                    if not self._begin(job, prefix + label):
                        return "cancelled"
                    found = self.live.search_many(aways, homes, day, searched, inbound=True)
                    self._combine_returns(job, request, day, found, wanted)
                    job["cursor"] = [index, 1, number + 1]
                    job["completed"] = base + outs + min(number + 1, schedule.back_bound)
                    job["return_requests"] += 1
                    self._advance(job, schedule, pace)
                job["return_days"] += 1
            for key in [k for k in job["outbound"] if schedule.last_back.get(k[-10:], "") <= day]:
                del job["outbound"][key]  # every return day of that outbound has been priced
            stage, batch = 0, 0
            job["cursor"] = [index + 1, 0, 0]
            job["completed"] = done + schedule.units_before(index + 1)
        return "complete"

    @staticmethod
    def _round_trip_key(trip):
        """Route and dates of a trip that one return ticket could cover, else None."""
        out, back = trip["outbound"]["segments"], (trip.get("inbound") or {}).get("segments")
        if not back:
            return None
        origin, destination = out[0]["from_code"], out[-1]["to_code"]
        if (back[0]["from_code"], back[-1]["to_code"]) != (destination, origin):
            return None
        return f"{origin}|{destination}|{out[0]['departure'][:10]}|{back[0]['departure'][:10]}"

    def _compare_round_trips(self, job, request):
        """Compare trips with Google's single-ticket return fares; False if stopped.

        One request covers a route on a pair of dates. The cheapest dates of
        every route come first, then the other dates by price, up to the cap.
        """
        price = {}
        for trip in job["results"].values():
            key = self._round_trip_key(trip)
            if key:
                price[key] = min(trip["price"], price.get(key, trip["price"]))
        by_price = sorted(price, key=lambda key: (price[key], key))
        first = {}
        for key in by_price:
            first.setdefault(key[:7], key)  # "VCE|BCN": the cheapest dates of each route
        ordered = [*first.values(), *(key for key in by_price if first[key[:7]] != key)]
        fares = job.setdefault("round_trips", {})
        keys = [key for key in ordered[:ROUND_TRIP_CHECKS] if key not in fares]
        base = job["completed"]
        job["total"] = base + len(keys)
        for number, key in enumerate(keys):
            origin, destination, out, back = key.split("|")
            label = f"Confronto con biglietto A/R: {origin} → {destination} · {out}"
            if not self._begin(job, label):
                return False
            found = self.live.round_trip(origin, destination, out, back, request)
            if found is not None:
                fares[key] = found
            job["completed"], job["eta_seconds"] = base + number + 1, len(keys) - number - 1
            self._progress(job)
        return True

    def _begin(self, job, label):
        if self.stopped(job["id"]):
            return False
        job["current"] = label
        self._progress(job)
        return True

    def _advance(self, job, schedule, pace):
        job["requests"] += 1
        pace["requests"] += 1
        measured = (time.monotonic() - pace["started"]) / pace["requests"]
        default = seconds_per_search(schedule.request)
        seconds = measured if pace["requests"] >= PACE_SAMPLE else default
        share = (
            job["return_requests"] / (job["return_days"] * schedule.back_bound)
            if job["return_days"] and schedule.back_bound
            else 1
        )
        left = schedule.remaining(job["cursor"], share) * seconds + pace["later"]
        job["eta_seconds"] = round(left)
        self._progress(job)

    def _keep_outbound(self, job, request, day, found):
        for (origin, destination), result in found.items():
            job["unverified"] += result["unverified"]
            job["empty_searches"] += int(result["empty"])
            outbound = [
                q
                for q in result["quotes"]
                if matches(q, request, origin, destination, day) and q["price"] <= request.budget
            ]
            if request.trip_type == "one_way":
                for quote in outbound:
                    trip = single_trip(quote, request)
                    job["results"][trip["id"]] = trip
            elif outbound:
                job["outbound"][f"{origin}|{destination}|{day}"] = outbound

    @staticmethod
    def _wanted_returns(job, request, schedule, day):
        """Return routes worth pricing on `day`, each with the outbounds it completes."""
        wanted = {}
        for out in schedule.outs_for[day]:
            for origin, destination in schedule.routes:
                outbound = job["outbound"].get(f"{origin}|{destination}|{out}")
                if outbound:
                    for route in return_routes(request, origin, destination):
                        wanted.setdefault(route, []).extend(outbound)
        return wanted

    @staticmethod
    def _combine_returns(job, request, day, found, wanted):
        for (away, home), result in found.items():
            job["unverified"] += result["unverified"]
            job["empty_searches"] += int(result["empty"])
            inbound = [
                q for q in result["quotes"] if matches(q, request, away, home, day, inbound=True)
            ]
            for a in wanted.get((away, home), ()):
                for b in inbound:
                    pair = combine(a, b, request)
                    if pair:
                        job["results"][pair["id"]] = pair

    def _progress(self, job):
        # A job of tens of MB takes seconds to write: never spend more than
        # about a fifth of the time saving.
        if time.monotonic() - self._saved_at < max(SAVE_INTERVAL, 5 * self._save_cost):
            return
        started = time.monotonic()
        job["updated_at"] = utc_now()
        with self._lock:
            status, rechecks = self.stored(job["id"], "status", "rechecks")
            if status == "cancelling":
                return
            job["rechecks"] = json.loads(rechecks or "{}")
            self.save(job)
        self._saved_at = time.monotonic()
        self._save_cost = self._saved_at - started

    def public(self, job_id, sort="price", offset=0, filters=None, focus=""):
        job = self.get(job_id)
        if job["status"] in ("running", "cancelling") and not (
            self._active_job_id == job_id and self._worker and self._worker.is_alive()
        ):
            job["status"] = "interrupted"
            job["issues"] = ["Server riavviato: premi Riprendi per continuare."]
        all_results = self._current_results(job)
        job["airports"] = result_airports({**job, "results": all_results})
        job.update(browse_results(all_results, sort, offset, filters, focus))
        request = FlexibleRequest(**job["request"])
        for item in job["results"]:  # only the visible page
            item["round_trip_url"] = round_trip_url(item, request)
        job["plan"] = {k: v for k, v in job["plan"].items() if k != "tasks"}
        for internal in ("outbound", "cursor", "round_trips"):
            job.pop(internal, None)
        return job

    @staticmethod
    def _current_results(job):
        """Saved trips with rechecks applied; removes results/rechecks from job."""
        results = job.pop("results")
        for result_id, check in job.pop("rechecks", {}).items():
            if check["status"] == "confirmed":
                results[result_id] = check["result"]
            else:
                results.pop(result_id, None)
        all_results = list(results.values())
        now = datetime.now(timezone.utc)
        fares = job.get("round_trips", {})
        for item in all_results:
            age = (now - datetime.fromisoformat(item["checked_at"])).total_seconds()
            item["needs_recheck"] = age > QUOTE_TTL
            # A cheaper single return ticket with this outbound is only a hint:
            # Google does not name the return it implies, so it may not be the
            # one of this trip. The price stays the one of these two flights.
            fare = fares.get(JobManager._round_trip_key(item) or "", {}).get(item["id"][:24])
            if fare and fare < item["price"]:
                item["round_trip_from"] = fare
        return all_results

    def export(self, job_id, filters=None):
        """Request and filtered trips for a PDF, cheapest first. No provider calls."""
        job = self.get(job_id)
        filters = filters or ResultFilters()
        trips = sorted(
            (t for t in self._current_results(job) if filters.matches(t)),
            key=lambda t: sort_key(t, "price"),
        )
        return job["request"], trips

    def recheck(self, job_id, result_id):
        job = self.get(job_id)
        pair = job["results"][result_id]
        previous_check = job.get("rechecks", {}).get(result_id)
        if previous_check and previous_check["status"] == "confirmed":
            pair = previous_check["result"]
        request = FlexibleRequest(**job["request"])
        selected = []
        for key in ("outbound", "inbound"):
            quote = pair[key]
            if quote is None:
                continue
            origin = quote["segments"][0]["from_code"]
            destination = quote["segments"][-1]["to_code"]
            day = quote["segments"][0]["departure"][:10]
            result = self.live.search(
                origin, destination, day, request, inbound=key == "inbound", fresh=True
            )
            match = next(
                (
                    q
                    for q in result["quotes"]
                    if fingerprint(q) == fingerprint(quote)
                    and matches(q, request, origin, destination, day, key == "inbound")
                ),
                None,
            )
            if match is None:
                return self._store_recheck(
                    job_id,
                    result_id,
                    {
                        "status": "not_confirmed",
                        "message": "Gli stessi voli non sono stati confermati con questi filtri. "
                        "Il risultato precedente non è più valido.",
                    },
                )
            selected.append(match)
        updated = (
            single_trip(selected[0], request)
            if request.trip_type == "one_way"
            else combine(*selected, request)
        )
        if updated is None:
            return self._store_recheck(
                job_id,
                result_id,
                {
                    "status": "outside_filters",
                    "message": "Il prezzo o gli orari non rispettano più i filtri.",
                },
            )
        return self._store_recheck(
            job_id,
            result_id,
            {
                "status": "confirmed",
                "result": updated,
                "price_changed": updated["price"] != pair["price"],
            },
        )

    def _store_recheck(self, job_id, result_id, check):
        with self._lock:
            job = self.get(job_id)
            job.setdefault("rechecks", {})[result_id] = check
            job["updated_at"] = utc_now()
            self.save(job)
        return check
