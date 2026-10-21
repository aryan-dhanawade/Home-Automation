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

from . import protocol
from .broker import make_client

log = logging.getLogger("presencesense.sim")

# Same values as firmware Config.h
PRESENCE_RANGE_CM = 150
RANGE_POLL_S = 0.08
GRACE_PERIOD_S = 6.0
# Wokwi photoresistor module: 10 kOhm series resistor, LDR gamma 0.7, 50 kOhm at 10 lux.
LDR_GAMMA = 0.7
LDR_RL10_KOHM = 50
ADC_VREF = 3.3
TELEMETRY_PERIOD_S = 5.0

FAR_CM = 400.0


def lux_to_adc(lux):
    """The raw 12-bit reading the Wokwi LDR module gives at this illuminance.
    More light -> lower LDR resistance -> lower voltage -> lower reading."""
    resistance = LDR_RL10_KOHM * 1e3 * 10**LDR_GAMMA / max(lux, 0.01) ** LDR_GAMMA
    volts = ADC_VREF * resistance / (resistance + 10_000)
    return round(volts / ADC_VREF * protocol.ADC_MAX)


def cycle_scenario(t):
    """(distance_cm, lux) at time t of a 120 s loop covering every state."""
    t %= 120
    lux = 80 * random.uniform(0.95, 1.05)
    if 60 <= t < 90:
        lux = 900 * random.uniform(0.97, 1.03)  # daylight
    if 15 <= t < 45 or 70 <= t < 100:
        return 85 + random.uniform(-8, 8), lux
    if 55 <= t < 55.1:
        return 60.0, lux  # single-sample jolt: must not trigger
    if 110 <= t < 112:
        return -1.0, lux  # echo timeout
    return FAR_CM, lux


def box_scenario(t):
    """A box parked 70 cm in front of the sensor: occupied forever, zero variance."""
    return 70.0, 80.0


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
        self.lux = 0.0
        self.daylight = False
        self.auto_state = "VACANT"
        self.grace_started = 0.0
        self.override = False
        self.lamps = {"lampA": 0, "lampB": 0}
        self.seq = 0
        self.last_reported_state = None

        self.client = make_client(
            settings,
            f"ps-sim-{settings.hub_id}-{random.randrange(1 << 16):04x}",
            will=(settings.topic("status"), "offline", 1, True),
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def uptime_ms(self):
        return int((time.monotonic() - self.started) * 1000)

    @property
    def state(self):
        return "OVERRIDE" if self.override else self.auto_state

    # --- OccupancyEngine ---------------------------------------------------------

    def update(self, t):
        distance, lux = self.scenario(t)
        self.distance = distance
        self.window.append(0 < distance <= PRESENCE_RANGE_CM)
        self.lux = lux
        if lux >= protocol.LUX_DAYLIGHT_ON:
            self.daylight = True
        elif lux < protocol.LUX_DAYLIGHT_OFF:
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
        """Returns the ack object, as the firmware's CommandRouter builds it."""
        cid = cmd.get("correlationId") if isinstance(cmd.get("correlationId"), str) else ""
        ack = {"result": "OK", "correlationId": cid[: protocol.CORRELATION_ID_MAX], "source": source}
        token = cmd.get("token") if isinstance(cmd.get("token"), str) else ""
        if not hmac.compare_digest(token.encode(), self.settings.api_token.encode()):
            log.warning("[%s] command rejected: bad token", source)
            return {**ack, "result": "AUTH_FAIL"}
        ctype, target, value = cmd.get("type"), cmd.get("target"), cmd.get("value")
        if ctype not in protocol.COMMAND_TYPES or (ctype != "resume" and target not in protocol.TARGETS):
            return {**ack, "result": "BAD_REQUEST"}
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
                    return {**ack, "result": "BAD_REQUEST"}
                self.override = True
                for lamp in ("lampA", "lampB") if target == "all" else (target,):
                    self.lamps[lamp] = level
        log.info("[%s] %s %s %s -> OK", source, ctype, target, value)
        if ctype == "resume":
            return {**ack, "type": "resume", "target": "all", "value": 0}
        return {**ack, "type": ctype, "target": target, "value": level}

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
        self.publish_json("ack", self.handle_command(cmd, "MQTT"), qos=1)

    def publish_json(self, leaf, obj, qos=0):
        self.client.publish(self.settings.topic(leaf), json.dumps(obj, separators=(",", ":")), qos=qos)

    def telemetry(self):
        return {
            "state": self.state,
            "lampA": self.lamps["lampA"],
            "lampB": self.lamps["lampB"],
            "ambient": lux_to_adc(self.lux),
            "lux": round(self.lux, 1),
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
                ack = hub.handle_command(cmd, "REST")
                hub.publish_json("ack", ack, qos=1)  # the MQTT side sees every command too
                self._send({"OK": 200, "AUTH_FAIL": 401}.get(ack["result"], 400), ack)

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
