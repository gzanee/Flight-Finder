"""HTTP interface for planning, progress, cancellation and fresh rechecks."""

from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, redirect, render_template, request

from . import airports, routes
from .flexible import FlexibleRequest, SearchValidationError, build_plan, integer
from .flexible_jobs import JobManager
from .live_quotes import LiveUnavailable, seconds_per_search
from .pdf_export import MAX_PER_CITY, PER_CITY, build_pdf
from .result_browser import ResultFilters

blueprint = Blueprint("flexible", __name__)
manager = JobManager()


def plan_request():
    spec = FlexibleRequest.parse(request.get_json(silent=True))
    route_map = candidate_routes(spec)
    plan = build_plan(spec, route_map)
    return spec, plan


def candidate_routes(spec):
    if spec.destinations:
        return {}
    if spec.destination_set != "real":
        return {origin: airports.destination_set(spec.destination_set) for origin in spec.origins}
    route_map = {origin: routes.destinations(origin) for origin in spec.origins}
    if not any(route_map.values()):
        # Seed pool for origins without a route catalog: every candidate needs
        # a live search; this does not imply a connection from that origin.
        snapshot = json.loads(routes.SNAPSHOT.read_text())
        route_map = {
            "catalogo_generale": sorted(
                {code for codes in snapshot.values() if isinstance(codes, list) for code in codes}
            )
        }
    return route_map


@blueprint.route("/")
def page():
    return render_template("weekends.html", destination_sets=airports.DESTINATION_SETS)


@blueprint.route("/weekends")
@blueprint.route("/classic")
def old_page():
    return redirect("/", code=302)


@blueprint.errorhandler(SearchValidationError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@blueprint.errorhandler(KeyError)
def missing(_exc):
    return jsonify(error="Ricerca o risultato non trovato."), 404


@blueprint.route("/api/flexible/plan", methods=["POST"])
@blueprint.route("/api/search/plan", methods=["POST"])
def preview():
    spec, plan = plan_request()
    visible = {k: v for k, v in plan.items() if k != "tasks"}
    seconds = plan["searches_upper_bound"] * seconds_per_search(spec)
    visible["estimated_minutes"] = round(seconds / 60, 1)
    return jsonify(request=spec.to_dict(), plan=visible)


@blueprint.route("/api/flexible/jobs", methods=["POST"])
@blueprint.route("/api/search/jobs", methods=["POST"])
def create():
    spec, plan = plan_request()
    try:
        job_id = manager.create(spec, plan)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(id=job_id), 202


@blueprint.route("/api/flexible/jobs/<job_id>")
@blueprint.route("/api/search/jobs/<job_id>")
def status(job_id):
    offset = integer(request.args.get("offset", 0), "pagina", 0, 10000000)
    return jsonify(
        manager.public(
            job_id,
            request.args.get("sort", "price"),
            offset,
            ResultFilters.parse(request.args),
            request.args.get("focus", ""),
        )
    )


@blueprint.route("/api/search/jobs/<job_id>/export.pdf")
def export_pdf(job_id):
    """The saved results under the current filters, as an easy-to-share PDF."""
    per_city = integer(
        request.args.get("per_city", PER_CITY), "Voli per destinazione", 1, MAX_PER_CITY
    )
    spec, trips = manager.export(job_id, ResultFilters.parse(request.args))
    return Response(
        build_pdf(spec, trips, per_city),
        mimetype="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="voli-weekend.pdf"'},
    )


@blueprint.route("/api/flexible/jobs/<job_id>/cancel", methods=["POST"])
@blueprint.route("/api/search/jobs/<job_id>/cancel", methods=["POST"])
def cancel(job_id):
    manager.cancel(job_id)
    return jsonify(success=True)


@blueprint.route("/api/flexible/jobs/<job_id>/resume", methods=["POST"])
@blueprint.route("/api/search/jobs/<job_id>/resume", methods=["POST"])
def resume(job_id):
    try:
        manager.resume(job_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(success=True), 202


@blueprint.route("/api/flexible/jobs/<job_id>/results/<result_id>/recheck", methods=["POST"])
@blueprint.route("/api/search/jobs/<job_id>/results/<result_id>/recheck", methods=["POST"])
def recheck(job_id, result_id):
    try:
        return jsonify(manager.recheck(job_id, result_id))
    except LiveUnavailable as exc:
        return jsonify(error=str(exc), status="unavailable"), 503
