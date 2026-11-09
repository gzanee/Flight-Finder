"""Resumable local search jobs. Persist progress; stop on blocks, never invent no availability."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .airports import details as airport_details
from .flexible import FlexibleRequest, combine, fingerprint, matches, return_routes, single_trip
from .live_quotes import QUOTE_TTL, LiveBlocked, LiveQuotes, LiveUnavailable, round_trip_url
from .result_browser import ResultFilters, browse_results, sort_key


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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
                "completed": 0,
                "total": len(plan["tasks"]),
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
        return self.get(job_id)["status"] == "cancelling"

    def run(self, job_id):  # noqa: PLR0912, PLR0915 - explicit cancellable task lifecycle
        job = self.get(job_id)
        request = FlexibleRequest(**job["request"])
        try:
            for task in job["plan"]["tasks"][job["completed"] :]:
                if self.stopped(job_id):
                    job["status"] = "cancelled"
                    break
                out, back, destination, origin = task
                job["current"] = f"{origin} → {destination} · {out}" + (
                    f" / {back}" if back else ""
                )
                self._progress(job)
                if self.stopped(job_id):
                    job["status"] = "cancelled"
                    break
                found = self.live.search(origin, destination, out, request)
                job["unverified"] += found["unverified"]
                job["empty_searches"] += int(found["empty"])
                outbound = [
                    q for q in found["quotes"] if matches(q, request, origin, destination, out)
                ]
                outbound = [q for q in outbound if q["price"] <= request.budget]
                if request.trip_type == "one_way":
                    for quote in outbound:
                        trip = single_trip(quote, request)
                        job["results"][trip["id"]] = trip
                if outbound:
                    for away, home in return_routes(request, origin, destination):
                        if self.stopped(job_id):
                            break
                        found_back = self.live.search(away, home, back, request, inbound=True)
                        job["unverified"] += found_back["unverified"]
                        job["empty_searches"] += int(found_back["empty"])
                        inbound = [
                            q
                            for q in found_back["quotes"]
                            if matches(q, request, away, home, back, inbound=True)
                        ]
                        for a in outbound:
                            for b in inbound:
                                pair = combine(a, b, request)
                                if pair:
                                    job["results"][pair["id"]] = pair
                if self.stopped(job_id):
                    job["status"] = "cancelled"
                    break  # current task will be retried on resume
                job["completed"] += 1
                self._progress(job)
            else:
                job["status"] = "complete"
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
            with self._lock:
                if self.get(job_id)["status"] == "cancelling":
                    job["status"] = "cancelled"
                job["rechecks"] = self.get(job_id).get("rechecks", {})
                self.save(job)

    def _progress(self, job):
        job["updated_at"] = utc_now()
        with self._lock:
            stored = self.get(job["id"])
            if stored["status"] == "cancelling":
                return
            job["rechecks"] = stored.get("rechecks", {})
            self.save(job)

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
        for item in all_results:
            age = (now - datetime.fromisoformat(item["checked_at"])).total_seconds()
            item["needs_recheck"] = age > QUOTE_TTL
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
