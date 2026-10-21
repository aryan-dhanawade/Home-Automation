import pytest

from presencesense import protocol
from presencesense.protocol import PayloadError


def test_valid_telemetry_normalised():
    t = protocol.validate_telemetry(
        {"state": "OCCUPIED", "lampA": 255, "lampB": 0, "ambient": 512, "uptime": 42, "distance": 88.5}
    )
    assert t["state"] == "OCCUPIED" and t["distance"] == 88.5 and t["fault"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "PARTY", "lampA": 0, "lampB": 0, "ambient": 0, "uptime": 0},
        {"state": "VACANT", "lampA": 256, "lampB": 0, "ambient": 0, "uptime": 0},
        {"state": "VACANT", "lampA": True, "lampB": 0, "ambient": 0, "uptime": 0},
        {"state": "VACANT", "lampA": "1", "lampB": 0, "ambient": 0, "uptime": 0},
        {"state": "VACANT", "lampA": 0, "lampB": 0, "ambient": 5000, "uptime": 0},
        {"state": "VACANT", "lampA": 0, "lampB": 0, "ambient": 0, "uptime": 0, "lux": -3},
        {"state": "VACANT", "lampA": 0, "lampB": 0, "ambient": 0},
        {"state": "VACANT", "lampA": 0, "lampB": 0, "ambient": 0, "uptime": 0, "fault": "yes"},
    ],
)
def test_invalid_telemetry_rejected(bad):
    with pytest.raises(PayloadError):
        protocol.validate_telemetry(bad)


def test_parse_json_limits():
    with pytest.raises(PayloadError):
        protocol.parse_json(b"[1, 2]")
    with pytest.raises(PayloadError):
        protocol.parse_json(b"{not json")
    with pytest.raises(PayloadError):
        protocol.parse_json(b"{" + b" " * protocol.MAX_PAYLOAD_BYTES + b"}")


def test_ack_and_status():
    assert protocol.validate_ack({"result": "ok", "correlationId": "c1"})["result"] == "OK"
    with pytest.raises(PayloadError):
        protocol.validate_ack({"result": "MAYBE", "correlationId": "c1"})
    with pytest.raises(PayloadError):
        protocol.validate_ack({"result": "OK", "correlationId": "x" * 24})
    full = protocol.validate_ack(
        {"result": "OK", "correlationId": "b1", "type": "on", "target": "lampA", "value": 255, "source": "BUTTON"}
    )
    assert (full["type"], full["target"], full["value"], full["source"]) == ("on", "lampA", 255, "BUTTON")
    with pytest.raises(PayloadError):
        protocol.validate_ack({"result": "OK", "correlationId": "b1", "source": "PIGEON"})
    assert protocol.validate_status(b"online") is True
    assert protocol.validate_status(b"offline") is False
    with pytest.raises(PayloadError):
        protocol.validate_status(b"rebooting")


def test_resolve_command():
    assert protocol.resolve_command("on", "lampA") == ("on", "lampA", 255, None)
    assert protocol.resolve_command("off", "all") == ("off", "all", 0, None)
    assert protocol.resolve_command("resume", None) == ("resume", "all", 0, None)
    assert protocol.resolve_command("scene", "lampB", scene="movie") == ("scene", "lampB", 64, "movie")
    assert protocol.resolve_command("brightness", "all", 10) == ("brightness", "all", 10, None)
    for args in [("brightness", "all", 300), ("brightness", "all", None), ("on", "lampC"), ("dance", "all")]:
        with pytest.raises(PayloadError):
            protocol.resolve_command(*args)


def test_command_fits_hub_buffers():
    cid = protocol.new_correlation_id()
    assert len(cid) <= protocol.CORRELATION_ID_MAX
    with pytest.raises(PayloadError):
        protocol.build_command("on", "all", 255, "t" * 65, cid)
