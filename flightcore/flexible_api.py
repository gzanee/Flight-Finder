"""HTTP interface for planning, progress, cancellation and fresh rechecks."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from . import routes
from .flexible import FlexibleRequest, SearchValidationError, build_plan, integer
from .flexible_jobs import JobManager
from .live_quotes import LiveUnavailable, request_interval

blueprint = Blueprint("flexible", __name__)
manager = JobManager()


def plan_request():
    spec = FlexibleRequest.parse(request.get_json(silent=True))
    route_map = (
        {}
        if spec.destinations
        else {origin: routes.destinations(origin) for origin in spec.origins}
    )
    plan = build_plan(spec, route_map)
    return spec, plan


@blueprint.route("/")
@blueprint.route("/weekends")
def page():
    return render_template("weekends.html")


@blueprint.errorhandler(SearchValidationError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@blueprint.errorhandler(KeyError)
def missing(_exc):
    return jsonify(error="Ricerca o risultato non trovato."), 404


@blueprint.route("/api/flexible/plan", methods=["POST"])
def preview():
    spec, plan = plan_request()
    visible = {k: v for k, v in plan.items() if k != "tasks"}
    visible["estimated_minutes"] = round(
        plan["searches_upper_bound"] * request_interval() / 60, 1
    )
    return jsonify(request=spec.to_dict(), plan=visible)


@blueprint.route("/api/flexible/jobs", methods=["POST"])
def create():
    spec, plan = plan_request()
    try:
        job_id = manager.create(spec, plan)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(id=job_id), 202


@blueprint.route("/api/flexible/jobs/<job_id>")
def status(job_id):
    offset = integer(request.args.get("offset", 0), "pagina", 0, 10000000)
    return jsonify(manager.public(job_id, request.args.get("sort", "price"), offset))


@blueprint.route("/api/flexible/jobs/<job_id>/cancel", methods=["POST"])
def cancel(job_id):
    manager.cancel(job_id)
    return jsonify(success=True)


@blueprint.route("/api/flexible/jobs/<job_id>/resume", methods=["POST"])
def resume(job_id):
    try:
        manager.resume(job_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(success=True), 202


@blueprint.route("/api/flexible/jobs/<job_id>/results/<result_id>/recheck", methods=["POST"])
def recheck(job_id, result_id):
    try:
        return jsonify(manager.recheck(job_id, result_id))
    except LiveUnavailable as exc:
        return jsonify(error=str(exc), status="unavailable"), 503
