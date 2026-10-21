"""Wire contract between the hub and the backend (see docs/protocol.md).

Every inbound payload is validated here before it touches the database; a
PayloadError means the message is rejected and logged, never persisted.
"""

import json
import secrets

STATES = ("VACANT", "OCCUPIED", "OCCUPIED_DAYLIGHT", "GRACE", "OVERRIDE")
OCCUPIED_STATES = frozenset({"OCCUPIED", "OCCUPIED_DAYLIGHT"})

COMMAND_TYPES = ("on", "off", "brightness", "scene", "resume")
TARGETS = ("lampA", "lampB", "all")
ACK_RESULTS = ("OK", "AUTH_FAIL", "BAD_REQUEST")
TRANSPORTS = ("MQTT", "REST")
# Where a command entered the hub. BUTTON = the hub's own wall switch.
COMMAND_SOURCES = TRANSPORTS + ("BUTTON",)

# SCENE is a named brightness on the hub; the name only exists on this side.
SCENES = {"bright": 255, "reading": 180, "movie": 64, "night": 20}

MAX_PAYLOAD_BYTES = 512
CORRELATION_ID_MAX = 23  # Command.correlationId[24] on the hub
TOKEN_MAX = 64  # Command.token[65] on the hub
ADC_MAX = 4095  # ESP32 12-bit ADC
LUX_MAX = 200_000

# Daylight inhibit thresholds; must match LUX_DAYLIGHT_ON/OFF in firmware Config.h.
LUX_DAYLIGHT_ON = 500
LUX_DAYLIGHT_OFF = 300


class PayloadError(ValueError):
    pass


def parse_json(payload):
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise PayloadError(f"payload too large ({len(payload)} B)")
    try:
        obj = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError(f"invalid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise PayloadError("payload must be a JSON object")
    return obj


def _int(obj, key, lo, hi, required=True):
    if key not in obj:
        if required:
            raise PayloadError(f"missing '{key}'")
        return None
    value = obj[key]
    # bool is a subclass of int; a JSON true is not a brightness.
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"'{key}' must be an integer")
    if not lo <= value <= hi:
        raise PayloadError(f"'{key}'={value} outside {lo}..{hi}")
    return value


def _number(obj, key, lo, hi):
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"'{key}' must be a number")
    if not lo <= value <= hi:
        raise PayloadError(f"'{key}'={value} outside {lo}..{hi}")
    return float(value)


def _state(obj):
    state = obj.get("state")
    if state not in STATES:
        raise PayloadError(f"unknown state {state!r}")
    return state


def _uptime(obj):
    return _int(obj, "uptime", 0, 2**32 - 1)


def validate_telemetry(obj):
    fault = obj.get("fault")
    if fault is not None and not isinstance(fault, bool):
        raise PayloadError("'fault' must be a boolean")
    return {
        "state": _state(obj),
        "lampA": _int(obj, "lampA", 0, 255),
        "lampB": _int(obj, "lampB", 0, 255),
        "ambient": _int(obj, "ambient", 0, ADC_MAX),
        "lux": _number(obj, "lux", 0, LUX_MAX),
        "uptime": _uptime(obj),
        "distance": _number(obj, "distance", -1, 1000),
        "fault": fault,
    }


def validate_event(obj):
    return {
        "seq": _int(obj, "seq", 1, 2**32 - 1),
        "state": _state(obj),
        "uptime": _uptime(obj),
    }


def validate_ack(obj):
    cid = obj.get("correlationId")
    if not isinstance(cid, str) or not 0 < len(cid) <= CORRELATION_ID_MAX:
        raise PayloadError("missing or invalid 'correlationId'")
    result = obj.get("result")
    if not isinstance(result, str) or result.upper() not in ACK_RESULTS:
        raise PayloadError(f"unknown result {result!r}")
    ack = {"correlationId": cid, "result": result.upper(), "type": None, "target": None,
           "value": None, "source": None}
    # Optional: the hub describes what it executed, so commands the backend did
    # not send (the wall button, a direct REST call) are still logged and audited.
    if "type" in obj:
        if obj["type"] not in COMMAND_TYPES:
            raise PayloadError(f"unknown ack type {obj['type']!r}")
        ack["type"] = obj["type"]
    if "target" in obj:
        if obj["target"] not in TARGETS:
            raise PayloadError(f"unknown ack target {obj['target']!r}")
        ack["target"] = obj["target"]
    if "value" in obj:
        ack["value"] = _int(obj, "value", 0, 255)
    if "source" in obj:
        if obj["source"] not in COMMAND_SOURCES:
            raise PayloadError(f"unknown ack source {obj['source']!r}")
        ack["source"] = obj["source"]
    return ack


def validate_status(payload):
    text = payload.decode("utf-8", errors="replace").strip().lower()
    if text not in ("online", "offline"):
        raise PayloadError(f"status must be online/offline, got {text[:20]!r}")
    return text == "online"


def new_correlation_id():
    return "c" + secrets.token_hex(8)


def resolve_command(cmd_type, target, value=None, scene=None):
    """Normalise a dashboard request into (type, target, value, label)."""
    if cmd_type not in COMMAND_TYPES:
        raise PayloadError(f"unknown command type {cmd_type!r}")
    if cmd_type == "resume":
        return "resume", "all", 0, None
    if target not in TARGETS:
        raise PayloadError(f"unknown target {target!r}")
    if cmd_type == "on":
        return "on", target, 255, None
    if cmd_type == "off":
        return "off", target, 0, None
    if cmd_type == "scene":
        if scene not in SCENES:
            raise PayloadError(f"unknown scene {scene!r}")
        return "scene", target, SCENES[scene], scene
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise PayloadError("brightness value must be an integer 0..255")
    return "brightness", target, value, None


def build_command(cmd_type, target, value, token, correlation_id):
    if not 0 < len(token) <= TOKEN_MAX:
        raise PayloadError(f"token must be 1..{TOKEN_MAX} chars")
    if not 0 < len(correlation_id) <= CORRELATION_ID_MAX:
        raise PayloadError("correlation id too long")
    return {
        "type": cmd_type,
        "target": target,
        "value": value,
        "token": token,
        "correlationId": correlation_id,
    }
