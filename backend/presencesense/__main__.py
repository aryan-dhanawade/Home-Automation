"""presencesense CLI.

    python -m presencesense serve       subscriber + rule engine + scheduler + dashboard
    python -m presencesense sim         simulated hub (stand-in for the firmware)
    python -m presencesense report      energy report from logged data (Markdown)
    python -m presencesense metrics     evaluation metrics as JSON
    python -m presencesense retention   run the 30-day aggregation once
    python -m presencesense netcheck    test whether this network can reach the broker
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .broker import is_local, random_hub_id
from .config import Settings, load_dotenv
from .protocol import TOKEN_MAX

MIN_PUBLIC_HUB_ID = 12


def _require_token(settings):
    token = settings.api_token
    if not token or token.lower() in ("change-me", "changeme"):
        sys.exit(
            "PS_API_TOKEN is not set. Generate one with\n"
            '  python -c "import secrets; print(secrets.token_hex(16))"\n'
            "and put it in backend/.env (and the same value in the firmware's Secrets.h)."
        )
    if len(token) > TOKEN_MAX:
        sys.exit(f"PS_API_TOKEN must be at most {TOKEN_MAX} characters (hub buffer limit).")


def _require_private_topics(settings):
    """On a public broker anyone can subscribe to home/hub01/#. A random hub id
    keeps casual readers and other students' hubs off our topics. (It is not
    encryption: a subscriber to '#' still sees everything.)"""
    if is_local(settings):
        return
    if settings.hub_id == "hub01" or len(settings.hub_id) < MIN_PUBLIC_HUB_ID:
        sys.exit(
            f"PS_HUB_ID={settings.hub_id!r} is too guessable for the public broker "
            f"{settings.mqtt_host}.\nUse a random one, e.g.\n  PS_HUB_ID={random_hub_id()}\n"
            "in backend/.env, and the same value as HUB_ID in the firmware's Secrets.h."
        )


def _window(args, now):
    until = args.until or now
    since = args.since or until - args.minutes * 60
    return since, until


def main(argv=None):
    parser = argparse.ArgumentParser(prog="presencesense")
    parser.add_argument("--env", default=".env", help="path to .env file (default: ./.env)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run backend and dashboard")

    sim = sub.add_parser("sim", help="run the simulated hub")
    sim.add_argument("--scenario", choices=("cycle", "box"), default="cycle")
    sim.add_argument("--rest-port", type=int, default=0, help="also serve the REST control plane")
    sim.add_argument("--drop-events", type=float, default=0.0, help="fraction of events to drop")

    for name in ("report", "metrics"):
        p = sub.add_parser(name)
        p.add_argument("--minutes", type=float, default=60)
        p.add_argument("--since", type=float, help="epoch seconds (overrides --minutes)")
        p.add_argument("--until", type=float, help="epoch seconds (default: now)")
        if name == "report":
            p.add_argument("--json", action="store_true")

    sub.add_parser("retention", help="aggregate data older than the retention window")
    sub.add_parser("netcheck", help="test whether this network can reach the broker")

    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    load_dotenv(Path(args.env))
    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if args.cmd == "sim":
        from .sim import SimulatedHub

        _require_token(settings)
        _require_private_topics(settings)
        hub = SimulatedHub(settings, scenario=args.scenario, drop_events=args.drop_events)
        if args.rest_port:
            hub.serve_rest(args.rest_port)
        hub.run()
        return

    if args.cmd == "netcheck":
        from .broker import netcheck

        sys.exit(0 if netcheck(settings) else 1)

    from .service import HubService

    service = HubService(settings)

    if args.cmd == "serve":
        from .web import create_app

        _require_token(settings)
        _require_private_topics(settings)
        service.start_mqtt()
        service.start_background()
        app = create_app(service)
        logging.getLogger("presencesense").info(
            "dashboard on http://%s:%s", settings.web_host, settings.web_port
        )
        try:
            app.run(host=settings.web_host, port=settings.web_port, threaded=True, use_reloader=False)
        finally:
            service.stop()
    elif args.cmd == "report":
        report = service.energy_report(*_window(args, time.time()))
        print(json.dumps(report.as_dict(), indent=2) if args.json else report.to_markdown())
    elif args.cmd == "metrics":
        since, _ = _window(args, time.time())
        print(json.dumps(service.metrics(since), indent=2))
    elif args.cmd == "retention":
        print(json.dumps(service.run_retention(), indent=2))


if __name__ == "__main__":
    main()
