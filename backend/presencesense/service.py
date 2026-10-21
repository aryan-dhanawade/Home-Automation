"""HubService: the backend's composition root.

Ingest (MQTT -> validate -> persist -> rules) and command dispatch (dashboard ->
MQTT or REST -> ack -> round-trip) both live here so the web layer stays thin
and everything is testable without a broker.
"""

import json
import logging
import secrets
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import protocol
from .broker import make_client
from .db import Database
from .energy import compute_report
from .protocol import PayloadError
from .retention import run_retention
from .rules import OverrideAuditor, SequenceTracker, StuckStateDetector

log = logging.getLogger("presencesense")

MQTT_RTT_TARGET_MS = 2000
REST_RTT_TARGET_MS = 500


class CommandError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class HubService:
    def __init__(self, settings, db=None, clock=time.time):
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.clock = clock
        self.device_id = settings.hub_id

        last = self.db.last_event(self.device_id)
        self._seq = SequenceTracker(
            last_seq=last["seq_num"] if last else None,
            last_uptime=last["device_uptime_ms"] if last else None,
            boot=last["boot"] if last else 0,
        )
        self._stuck = StuckStateDetector(
            settings.stuck_window_s,
            settings.stuck_distance_ceiling_cm,
            settings.stuck_duration_only_s,
        )
        self._audit = OverrideAuditor()

        # paho runs callbacks on its own thread; the web layer calls in from
        # request threads. One lock keeps rule state consistent.
        self._lock = threading.Lock()
        self._waiters = {}
        self._client = None
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._threads = []

    # --- MQTT plumbing ---------------------------------------------------------

    @property
    def broker_connected(self):
        return self._connected.is_set()

    def attach_client(self, client, connected=True):
        """Use an already-built client (tests inject a fake)."""
        self._client = client
        if connected:
            self._connected.set()

    def start_mqtt(self):
        s = self.settings
        # A random suffix: two backends with the same client id on a public
        # broker would keep kicking each other off.
        client = make_client(s, f"{s.mqtt_client_id}-{secrets.token_hex(3)}")
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        log.info(
            "MQTT connecting to %s:%s (%s, tls=%s)", s.mqtt_host, s.mqtt_port, s.mqtt_transport, s.mqtt_tls
        )

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect refused: %s", reason_code)
            return
        self._connected.set()
        client.subscribe([(self.settings.topic(t), 1) for t in ("status", "telemetry", "event", "ack")])
        log.info("MQTT connected, subscribed under %s/#", self.settings.topic_root)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected.clear()
        log.warning("MQTT disconnected: %s (paho will retry with backoff)", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            self.ingest(msg.topic, msg.payload)
        except Exception:  # never let one message kill the network thread
            log.exception("ingest failed for %s", msg.topic)

    # --- ingest ----------------------------------------------------------------

    def ingest(self, topic, payload, now=None):
        now = self.clock() if now is None else now
        prefix = self.settings.topic_root + "/"
        leaf = topic[len(prefix):] if topic.startswith(prefix) else None
        handler = {
            "status": self._ingest_status,
            "telemetry": self._ingest_telemetry,
            "event": self._ingest_event,
            "ack": self._ingest_ack,
        }.get(leaf)
        try:
            if handler is None:
                raise PayloadError("unexpected topic")
            if len(payload) > protocol.MAX_PAYLOAD_BYTES:
                raise PayloadError(f"payload too large ({len(payload)} B)")
            with self._lock:
                handler(payload, now)
        except PayloadError as exc:
            excerpt = payload[:120].decode("utf-8", errors="replace")
            self.db.log_rejected(topic, str(exc), excerpt, now)
            log.warning("rejected message on %s: %s", topic, exc)

    def _ingest_status(self, payload, now):
        online = protocol.validate_status(payload)
        self.db.set_device_online(self.device_id, online, now)
        if online:
            self._clear_alert("device_offline", now)
        else:
            self._raise_alert("device_offline", "hub reported offline (Last Will or clean shutdown)", now)

    def _ingest_telemetry(self, payload, now):
        t = protocol.validate_telemetry(protocol.parse_json(payload))
        self.db.insert_telemetry(self.device_id, t, now)
        self.db.touch_device(self.device_id, now)

        outcome = self._stuck.observe(now, t["state"], t["distance"])
        if outcome and outcome[0] == "raise":
            self._raise_alert("stuck_state", outcome[1], now)
        elif outcome and outcome[0] == "clear":
            self._clear_alert("stuck_state", now)

        if t["fault"]:
            self._raise_alert("sensor_fault", "ranging sensor timed out (no echo)", now)
        elif t["fault"] is False:
            self._clear_alert("sensor_fault", now)

        violation = self._audit.check(t)
        if violation:
            self._raise_alert("override_violation", violation, now)
        else:
            self._clear_alert("override_violation", now)

    def _ingest_event(self, payload, now):
        e = protocol.validate_event(protocol.parse_json(payload))
        obs = self._seq.observe(e["seq"], e["uptime"])
        if obs.duplicate:
            log.info("duplicate event seq=%s ignored", e["seq"])
            return
        if obs.rebooted:
            log.warning("hub reboot detected (seq restarted at %s)", e["seq"])
            self._audit.on_reboot()
        if obs.missing:
            self.db.add_gap(self.device_id, obs.boot, e["seq"] - obs.missing - 1, obs.missing, now)
            log.warning("event sequence gap: %s missing before seq=%s", obs.missing, e["seq"])
        self.db.insert_event(self.device_id, obs.boot, e["seq"], e["state"], e["uptime"], now)
        self.db.touch_device(self.device_id, now)

    def _ingest_ack(self, payload, now):
        a = protocol.validate_ack(protocol.parse_json(payload))
        row = self.db.ack_command(a["correlationId"], a["result"], now)
        if row is None:
            # A command the backend did not send: the hub's wall button, or someone
            # commanding the hub directly. Log it so the audit trail is complete.
            source = a["source"] or "MQTT"
            origin = "hub" if source == "BUTTON" else "external"
            self.db.insert_command(
                a["correlationId"], a["type"], a["target"], a["value"], None, source, origin, None
            )
            row = self.db.ack_command(a["correlationId"], a["result"], now)
        if row["result"] == "OK" and row["cmd_type"]:
            self._audit.on_ack(row["cmd_type"], row["target"], row["value"])
        waiter = self._waiters.get(a["correlationId"])
        if waiter:
            waiter.set()

    def _raise_alert(self, kind, detail, now):
        if not self.db.active_alert(self.device_id, kind):
            self.db.raise_alert(self.device_id, kind, detail, now)
            log.warning("ALERT %s: %s", kind, detail)

    def _clear_alert(self, kind, now):
        self.db.clear_alert(self.device_id, kind, now)

    # --- commands --------------------------------------------------------------

    def send_command(self, cmd_type, target, value=None, scene=None, transport="MQTT",
                     origin="dashboard", wait=True):
        try:
            cmd_type, target, value, label = protocol.resolve_command(cmd_type, target, value, scene)
        except PayloadError as exc:
            raise CommandError(str(exc)) from None
        if transport not in protocol.TRANSPORTS:
            raise CommandError(f"unknown transport {transport!r}")
        if transport == "REST" and not self.settings.device_url:
            raise CommandError("REST transport disabled: set PS_DEVICE_URL", status=503)

        cid = protocol.new_correlation_id()
        body = json.dumps(
            protocol.build_command(cmd_type, target, value, self.settings.api_token, cid),
            separators=(",", ":"),
        )
        self.db.insert_command(cid, cmd_type, target, value, label, transport, origin, self.clock())
        if transport == "MQTT":
            self._send_mqtt(cid, body, wait)
        else:
            self._send_rest(cid, body)
        return self.db.get_command(cid)

    def _send_mqtt(self, cid, body, wait):
        if self._client is None or not self.broker_connected:
            self.db.fail_command(cid, "BROKER_DOWN")
            raise CommandError("MQTT broker not connected", status=503)
        waiter = threading.Event()
        self._waiters[cid] = waiter
        try:
            self._client.publish(self.settings.topic("cmd"), body, qos=1)
            if wait:
                waiter.wait(self.settings.command_timeout_s)
        finally:
            self._waiters.pop(cid, None)

    def _send_rest(self, cid, body):
        request = urllib.request.Request(
            self.settings.device_url + "/command",
            data=body.encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.settings.command_timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:  # 401/400 still carry {result, correlationId}
            raw = exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.db.fail_command(cid, "UNREACHABLE")
            raise CommandError(f"hub REST endpoint unreachable: {exc}", status=502) from None
        rtt_ms = (time.perf_counter() - started) * 1000
        try:
            ack = protocol.validate_ack(protocol.parse_json(raw))
        except PayloadError as exc:
            self.db.fail_command(cid, "BAD_RESPONSE")
            raise CommandError(f"hub returned an invalid response: {exc}", status=502) from None
        with self._lock:
            row = self.db.ack_command(cid, ack["result"], self.clock(), rtt_ms=rtt_ms)
            if row["result"] == "OK":
                self._audit.on_ack(row["cmd_type"], row["target"], row["value"])

    # --- schedules -------------------------------------------------------------

    def schedule_command(self, due_ts, cmd_type, target, value=None, scene=None, transport="MQTT"):
        try:
            cmd_type, target, value, label = protocol.resolve_command(cmd_type, target, value, scene)
        except PayloadError as exc:
            raise CommandError(str(exc)) from None
        if transport not in protocol.TRANSPORTS:
            raise CommandError(f"unknown transport {transport!r}")
        now = self.clock()
        if due_ts <= now:
            raise CommandError("schedule time must be in the future")
        return self.db.add_schedule(due_ts, cmd_type, target, value, label, transport, now)

    def cancel_schedule(self, schedule_id):
        return self.db.mark_schedule(schedule_id, "cancelled")

    def run_due_schedules(self):
        for row in self.db.due_schedules(self.clock()):
            try:
                cmd = self.send_command(
                    row["cmd_type"], row["target"], row["value"], row["label"],
                    transport=row["transport"], origin="schedule", wait=False,
                )
                self.db.mark_schedule(row["id"], "sent", cmd["correlation_id"])
                log.info("schedule %s sent as %s", row["id"], cmd["correlation_id"])
            except CommandError as exc:
                self.db.mark_schedule(row["id"], "failed")
                log.error("schedule %s failed: %s", row["id"], exc)

    # --- background jobs -------------------------------------------------------

    def run_retention(self):
        s = self.settings
        result = run_retention(self.db, self.clock(), s.retention_days, s.max_gap_s, s.telemetry_period_s)
        log.info("retention: %s", result)
        return result

    def start_background(self):
        def scheduler():
            while not self._stop.wait(1.0):
                try:
                    self.run_due_schedules()
                except Exception:
                    log.exception("scheduler tick failed")

        def retention():
            while True:
                try:
                    self.run_retention()
                except Exception:
                    log.exception("retention run failed")
                if self._stop.wait(3600):
                    return

        for fn in (scheduler, retention):
            t = threading.Thread(target=fn, name=fn.__name__, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        if self._client is not None and hasattr(self._client, "loop_stop"):
            self._client.disconnect()
            self._client.loop_stop()

    # --- read models for the dashboard -------------------------------------------

    def snapshot(self):
        now = self.clock()
        status = self.db.device_status(self.device_id)
        latest = self.db.latest_telemetry(self.device_id)
        if status is None:
            liveness = "unknown"
        elif not status["online"]:
            liveness = "offline"
        elif status["last_seen"] is None or now - status["last_seen"] > 3 * self.settings.telemetry_period_s:
            liveness = "stale"
        else:
            liveness = "online"
        return {
            "now": now,
            "hub_id": self.device_id,
            "broker_connected": self.broker_connected,
            "rest_enabled": bool(self.settings.device_url),
            "liveness": liveness,
            "last_seen": status["last_seen"] if status else None,
            "telemetry": latest,
            "events": self.db.recent_events(self.device_id),
            "commands": self.db.recent_commands(),
            "alerts": self.db.recent_alerts(self.device_id),
            "schedules": self.db.pending_schedules(),
            "scenes": protocol.SCENES,
            "daylight_lux": {"on": protocol.LUX_DAYLIGHT_ON, "off": protocol.LUX_DAYLIGHT_OFF},
        }

    def history(self, minutes, max_points=720):
        now = self.clock()
        rows = self.db.telemetry_between(self.device_id, now - minutes * 60, now)
        stride = max(1, len(rows) // max_points)
        return [
            [r["server_ts"], r["brightness_a"], r["brightness_b"], r["lux"], r["state"]]
            for r in rows[::stride]
        ]

    def energy_report(self, since, until):
        s = self.settings
        rows = self.db.telemetry_between(self.device_id, since - s.max_gap_s, until)
        samples = [(r["server_ts"], r["brightness_a"], r["brightness_b"]) for r in rows]
        daily = self.db.telemetry_daily_between(
            self.device_id,
            datetime.fromtimestamp(since).date().isoformat(),
            datetime.fromtimestamp(until).date().isoformat(),
        )
        return compute_report(
            samples, since, until, (s.lamp_a_watts, s.lamp_b_watts), s.hub_watts,
            s.max_gap_s, s.telemetry_period_s, daily,
        )

    def metrics(self, since):
        received, missing = self.db.delivery_counts(self.device_id, since)
        total = received + missing
        commands = self.db.commands_since(since)

        def rtt_stats(transport, target_ms):
            rtts = sorted(
                c["rtt_ms"] for c in commands if c["source"] == transport and c["rtt_ms"] is not None
            )
            sent = [c for c in commands if c["source"] == transport and c["origin"] != "external"]
            stats = {
                "sent": len(sent),
                "acked": sum(1 for c in sent if c["acked_at"] is not None),
                "ok": sum(1 for c in sent if c["result"] == "OK"),
                "target_ms": target_ms,
                "within_target": sum(1 for r in rtts if r < target_ms),
            }
            if rtts:
                stats.update(
                    median_ms=round(statistics.median(rtts), 1),
                    p95_ms=round(rtts[min(len(rtts) - 1, int(0.95 * len(rtts)))], 1),
                    max_ms=round(rtts[-1], 1),
                )
            return stats

        return {
            "since": since,
            "events_received": received,
            "events_missing": missing,
            "delivery_pct": round(100 * received / total, 1) if total else None,
            "mqtt": rtt_stats("MQTT", MQTT_RTT_TARGET_MS),
            "rest": rtt_stats("REST", REST_RTT_TARGET_MS),
            "override_violations": self.db.count(
                "alerts", since, "raised_ts", "kind = 'override_violation'"
            ),
            "device_auth_failures": sum(1 for c in commands if c["result"] == "AUTH_FAIL"),
            "dashboard_auth_rejections": self.db.count("auth_rejections", since),
            "rejected_messages": self.db.count("rejected_messages", since),
        }
