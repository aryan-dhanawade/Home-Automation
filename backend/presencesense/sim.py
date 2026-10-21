"""Simulated PresenceSense hub.

A Python stand-in for the ESP8266 firmware that speaks exactly the wire
contract in docs/protocol.md: LWT + retained status, 5 s telemetry,
sequence-numbered events, token-checked commands with correlated acks, the
override flag, and (optionally) the REST control plane. It mirrors the
firmware's OccupancyEngine so the backend and dashboard can be developed and
demonstrated before the Wokwi build exists.
"""

import hmac
import json
import logging
import random
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

from . import protocol

log = logging.getLogger("presencesense.sim")

# Same values as firmware Config.h
PRESENCE_RANGE_CM = 150
RANGE_POLL_S = 0.08
LDR_INHIBIT_ON = 600
LDR_INHIBIT_OFF = 450
GRACE_PERIOD_S = 6.0
TELEMETRY_PERIOD_S = 5.0

FAR_CM = 400.0


def cycle_scenario(t):
    """(distance_cm, ambient_raw) at time t of a 120 s loop covering every state."""
    t %= 120
    ambient = 300
    if 60 <= t < 90:
        ambient = 750  # daylight
    if 15 <= t < 45 or 70 <= t < 100:
        return 85 + random.uniform(-8, 8), ambient + random.randint(-10, 10)
    if 55 <= t < 55.1:
        return 60.0, ambient  # single-sample jolt: must not trigger
    if 110 <= t < 112:
        return -1.0, ambient  # echo timeout
    return FAR_CM, ambient + random.randint(-10, 10)


def box_scenario(t):
    """A box parked 70 cm in front of the sensor: occupied forever, zero variance."""
    return 70.0, 300


SCENARIOS = {"cycle": cycle_scenario, "box": box_scenario}


class SimulatedHub:
    def __init__(self, settings, scenario="cycle", drop_events=0.0):
        self.settings = settings
        self.scenario = SCENARIOS[scenario]
        self.drop_events = drop_events
        self.started = time.monotonic()
        self.lock = threading.Lock()

        self.window = deque(maxlen=3)
        self.distance = 0.0
        self.ambient = 0
        self.daylight = False
        self.auto_state = "VACANT"
        self.grace_started = 0.0
        self.override = False
        self.lamps = {"lampA": 0, "lampB": 0}
        self.seq = 0
        self.last_reported_state = None

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"ps-sim-{settings.hub_id}-{random.randrange(1 << 16):04x}"
        )
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
        if settings.mqtt_tls:
            self.client.tls_set(ca_certs=settings.mqtt_ca_file or None)
        self.client.will_set(settings.topic("status"), "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)

    def uptime_ms(self):
        return int((time.monotonic() - self.started) * 1000)

    @property
    def state(self):
        return "OVERRIDE" if self.override else self.auto_state

    # --- OccupancyEngine ---------------------------------------------------------

    def update(self, t):
        distance, ambient = self.scenario(t)
        self.distance = distance
        self.window.append(0 < distance <= PRESENCE_RANGE_CM)
        self.ambient = max(0, min(protocol.ADC_MAX, ambient))
        if self.ambient >= LDR_INHIBIT_ON:
            self.daylight = True
        elif self.ambient < LDR_INHIBIT_OFF:
            self.daylight = False

        if self.override:
            return  # Objective 7: automation never touches the lamps under override

        present = len(self.window) == 3 and all(self.window)
        occupied = "OCCUPIED_DAYLIGHT" if self.daylight else "OCCUPIED"
        now = time.monotonic()
        if present:
            self.auto_state = occupied
        elif self.auto_state in protocol.OCCUPIED_STATES:
            self.auto_state = "GRACE"
            self.grace_started = now
        elif self.auto_state == "GRACE" and now - self.grace_started >= GRACE_PERIOD_S:
            self.auto_state = "VACANT"

        if self.auto_state != "GRACE":  # grace holds the current level
            level = 255 if self.auto_state == "OCCUPIED" else 0
            self.lamps = {"lampA": level, "lampB": level}

    # --- CommandRouter (shared by both transports) -----------------------------------

    def handle_command(self, cmd, source):
        cid = cmd.get("correlationId") if isinstance(cmd.get("correlationId"), str) else ""
        cid = cid[: protocol.CORRELATION_ID_MAX]
        token = cmd.get("token") if isinstance(cmd.get("token"), str) else ""
        if not hmac.compare_digest(token.encode(), self.settings.api_token.encode()):
            log.warning("[%s] command rejected: bad token", source)
            return "AUTH_FAIL", cid
        ctype, target, value = cmd.get("type"), cmd.get("target"), cmd.get("value")
        if ctype not in protocol.COMMAND_TYPES or (ctype != "resume" and target not in protocol.TARGETS):
            return "BAD_REQUEST", cid
        with self.lock:
            if ctype == "resume":
                self.override = False
            else:
                if ctype == "on":
                    level = 255
                elif ctype == "off":
                    level = 0
                elif isinstance(value, int) and not isinstance(value, bool):
                    level = max(0, min(255, value))
                else:
                    return "BAD_REQUEST", cid
                self.override = True
                for lamp in ("lampA", "lampB") if target == "all" else (target,):
                    self.lamps[lamp] = level
        log.info("[%s] %s %s %s -> OK", source, ctype, target, value)
        return "OK", cid

    # --- MQTT ----------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("sim MQTT connect refused: %s", reason_code)
            return
        client.publish(self.settings.topic("status"), "online", qos=1, retain=True)
        client.subscribe(self.settings.topic("cmd"), qos=1)
        log.info("sim hub online at %s", self.settings.topic_root)

    def _on_message(self, client, userdata, msg):
        try:
            cmd = json.loads(msg.payload)
            if not isinstance(cmd, dict):
                raise ValueError
        except ValueError:
            return
        result, cid = self.handle_command(cmd, "MQTT")
        client.publish(
            self.settings.topic("ack"), json.dumps({"result": result, "correlationId": cid}), qos=1
        )

    def publish_json(self, leaf, obj, qos=0):
        self.client.publish(self.settings.topic(leaf), json.dumps(obj, separators=(",", ":")), qos=qos)

    def telemetry(self):
        return {
            "state": self.state,
            "lampA": self.lamps["lampA"],
            "lampB": self.lamps["lampB"],
            "ambient": self.ambient,
            "distance": round(self.distance, 1),
            "fault": self.distance < 0,
            "uptime": self.uptime_ms(),
        }

    # --- REST control plane ----------------------------------------------------------

    def serve_rest(self, port):
        hub = self
        origin = self.settings.dashboard_origin

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/status":
                    with hub.lock:
                        self._send(200, hub.telemetry())
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/command":
                    return self._send(404, {"error": "not found"})
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    cmd = json.loads(self.rfile.read(min(length, protocol.MAX_PAYLOAD_BYTES)))
                    if not isinstance(cmd, dict):
                        raise ValueError
                except ValueError:
                    return self._send(400, {"result": "BAD_REQUEST", "correlationId": ""})
                result, cid = hub.handle_command(cmd, "REST")
                code = {"OK": 200, "AUTH_FAIL": 401}.get(result, 400)
                self._send(code, {"result": result, "correlationId": cid})

            def log_message(self, fmt, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True, name="sim-rest").start()
        log.info("sim REST control plane on http://127.0.0.1:%s", port)

    # --- main loop -------------------------------------------------------------------

    def run(self):
        s = self.settings
        self.client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=15)
        self.client.loop_start()
        next_telemetry = time.monotonic()
        try:
            while True:
                t = time.monotonic() - self.started
                with self.lock:
                    self.update(t)
                    state = self.state
                    if state != self.last_reported_state:
                        self.last_reported_state = state
                        self.seq += 1
                        if random.random() >= self.drop_events:
                            self.publish_json("event", {"seq": self.seq, "state": state, "uptime": self.uptime_ms()})
                        else:
                            log.info("dropping event seq=%s (simulated loss)", self.seq)
                        log.info("state -> %s", state)
                    if time.monotonic() >= next_telemetry:
                        next_telemetry += TELEMETRY_PERIOD_S
                        self.publish_json("telemetry", self.telemetry())
                time.sleep(RANGE_POLL_S)
        except KeyboardInterrupt:
            pass
        finally:
            # Clean shutdown publishes offline explicitly; a crash leaves it to the LWT.
            self.client.publish(s.topic("status"), "offline", qos=1, retain=True).wait_for_publish(2)
            self.client.disconnect()
            self.client.loop_stop()
