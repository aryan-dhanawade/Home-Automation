"""Flask dashboard and JSON API.

Every /api route needs the bearer token: occupancy history reveals when people
are home, so reads are protected as well as commands. CORS is restricted to the
exact dashboard origin (a browser policy, not access control; the token is).
"""

import hmac
import logging
import time
from functools import wraps

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

from ..service import CommandError

log = logging.getLogger("presencesense.web")

MAX_HISTORY_MINUTES = 7 * 24 * 60


def create_app(service):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096
    settings = service.settings
    CORS(
        app,
        resources={r"/api/*": {"origins": [settings.dashboard_origin]}},
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "DELETE"],
    )
    expected = settings.api_token.encode()

    def require_token(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            given = header[7:].encode() if header.startswith("Bearer ") else b""
            # compare_digest is constant-time; an empty expected token never matches.
            if not expected or not hmac.compare_digest(given, expected):
                service.db.log_auth_rejection(request.remote_addr, request.path, time.time())
                log.warning("auth rejected: %s %s from %s", request.method, request.path, request.remote_addr)
                return jsonify(error="unauthorized"), 401
            return view(*args, **kwargs)

        return wrapper

    def minutes_arg(default):
        try:
            minutes = float(request.args.get("minutes", default))
        except ValueError:
            minutes = default
        return min(max(minutes, 1), MAX_HISTORY_MINUTES)

    def window_args():
        now = time.time()
        try:
            until = float(request.args["until"]) if "until" in request.args else now
            since = float(request.args["since"]) if "since" in request.args else None
        except ValueError:
            since, until = None, now
        if since is None:
            since = until - minutes_arg(60) * 60
        return since, until

    def command_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise CommandError("expected a JSON object")
        return body

    @app.errorhandler(CommandError)
    def command_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.get("/")
    def index():
        return render_template("index.html", hub_id=settings.hub_id)

    @app.post("/api/auth/check")
    @require_token
    def auth_check():
        return "", 204

    @app.get("/api/snapshot")
    @require_token
    def snapshot():
        return jsonify(service.snapshot())

    @app.get("/api/history")
    @require_token
    def history():
        return jsonify(points=service.history(minutes_arg(60)))

    @app.get("/api/energy")
    @require_token
    def energy():
        return jsonify(service.energy_report(*window_args()).as_dict())

    @app.get("/api/energy.md")
    @require_token
    def energy_markdown():
        report = service.energy_report(*window_args())
        return Response(
            report.to_markdown(),
            mimetype="text/markdown",
            headers={"Content-Disposition": "attachment; filename=energy-report.md"},
        )

    @app.get("/api/metrics")
    @require_token
    def metrics():
        since, _ = window_args()
        return jsonify(service.metrics(since))

    @app.post("/api/command")
    @require_token
    def command():
        body = command_body()
        row = service.send_command(
            body.get("type"),
            body.get("target"),
            body.get("value"),
            body.get("scene"),
            transport=body.get("transport", "MQTT"),
        )
        return jsonify(row), 200 if row["acked_at"] else 202

    @app.post("/api/schedule")
    @require_token
    def schedule():
        body = command_body()
        try:
            due_ts = float(body["due_ts"])
        except (KeyError, TypeError, ValueError):
            raise CommandError("due_ts (epoch seconds) is required") from None
        schedule_id = service.schedule_command(
            due_ts,
            body.get("type"),
            body.get("target"),
            body.get("value"),
            body.get("scene"),
            transport=body.get("transport", "MQTT"),
        )
        return jsonify(id=schedule_id), 201

    @app.delete("/api/schedule/<int:schedule_id>")
    @require_token
    def cancel_schedule(schedule_id):
        if not service.cancel_schedule(schedule_id):
            return jsonify(error="no pending schedule with that id"), 404
        return "", 204

    return app
