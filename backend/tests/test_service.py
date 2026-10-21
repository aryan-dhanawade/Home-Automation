import json

import pytest

from presencesense.service import CommandError

from .conftest import FakeHubClient, event, telemetry


def topic(service, leaf):
    return service.settings.topic(leaf)


def test_telemetry_persisted_and_malformed_rejected(service):
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 255, 255))
    service.ingest(topic(service, "telemetry"), b'{"state":"OCCUPIED","lampA":"bright"}')
    service.ingest("home/other/telemetry", telemetry())
    conn = service.db.conn()
    assert conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rejected_messages").fetchone()[0] == 2
    assert service.snapshot()["liveness"] == "online"


def test_liveness_from_lwt_and_staleness(service, clock):
    service.ingest(topic(service, "status"), b"online")
    assert service.snapshot()["liveness"] == "online"
    clock.advance(30)
    assert service.snapshot()["liveness"] == "stale"
    service.ingest(topic(service, "status"), b"offline")
    snap = service.snapshot()
    assert snap["liveness"] == "offline"
    assert snap["alerts"][0]["kind"] == "device_offline"


def test_event_gaps_feed_delivery_metric(service, clock):
    since = clock.now - 1
    for seq in (1, 2, 3, 5, 6, 10):
        service.ingest(topic(service, "event"), event(seq, uptime=seq * 1000))
    service.ingest(topic(service, "event"), event(10, uptime=10_000))  # duplicate
    m = service.metrics(since)
    assert m["events_received"] == 6
    assert m["events_missing"] == 4
    assert m["delivery_pct"] == 60.0


def test_tracker_resumes_from_database_after_restart(settings, service, clock):
    from presencesense.service import HubService

    service.ingest(topic(service, "event"), event(7, uptime=7000))
    restarted = HubService(settings, service.db, clock=clock)
    restarted.ingest(topic(restarted, "event"), event(9, uptime=9000))
    assert restarted.metrics(0)["events_missing"] == 1


def test_mqtt_command_round_trip(service):
    fake = FakeHubClient(service)
    service.attach_client(fake)
    row = service.send_command("brightness", "lampA", 100)
    assert row["result"] == "OK" and row["rtt_ms"] is not None
    sent_topic, payload = fake.published[0]
    assert sent_topic == "home/hub01/cmd"
    assert payload == {
        "type": "brightness", "target": "lampA", "value": 100,
        "token": service.settings.api_token, "correlationId": row["correlation_id"],
    }


def test_mqtt_command_timeout_then_late_ack(service):
    fake = FakeHubClient(service, respond=False)
    service.attach_client(fake)
    row = service.send_command("on", "all")
    assert row["acked_at"] is None
    cid = row["correlation_id"]
    service.ingest(topic(service, "ack"), json.dumps({"result": "OK", "correlationId": cid}).encode())
    assert service.db.get_command(cid)["result"] == "OK"


def test_command_without_broker_fails_cleanly(service):
    with pytest.raises(CommandError) as exc:
        service.send_command("on", "all")
    assert exc.value.status == 503
    assert service.db.recent_commands()[0]["result"] == "BROKER_DOWN"


def test_rest_disabled_without_device_url(service):
    with pytest.raises(CommandError):
        service.send_command("on", "all", transport="REST")


def test_override_violation_detected(service):
    service.attach_client(FakeHubClient(service))
    service.send_command("off", "all")
    service.ingest(topic(service, "telemetry"), telemetry("OVERRIDE", 0, 0))
    assert service.metrics(0)["override_violations"] == 0
    # Hub's automation flips the lamp back on while the override should hold.
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 255, 255))
    assert service.metrics(0)["override_violations"] == 1
    service.send_command("resume", None)
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 255, 255))
    assert service.metrics(0)["override_violations"] == 1


def test_auth_fail_ack_does_not_engage_audit(service):
    service.attach_client(FakeHubClient(service, result="AUTH_FAIL"))
    row = service.send_command("off", "all")
    assert row["result"] == "AUTH_FAIL"
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 255, 255))
    m = service.metrics(0)
    assert m["override_violations"] == 0 and m["device_auth_failures"] == 1


def test_stuck_state_and_fault_alerts(service, clock):
    for i in range(14):
        service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 255, 255, distance=70.0, fault=False))
        clock.advance(5)
    service.ingest(topic(service, "telemetry"), telemetry("GRACE", 255, 255, distance=-1, fault=True))
    kinds = {a["kind"]: a for a in service.snapshot()["alerts"]}
    assert kinds["stuck_state"]["cleared_ts"] is not None
    assert kinds["sensor_fault"]["cleared_ts"] is None


def test_external_ack_logged(service):
    service.ingest(topic(service, "ack"), b'{"result":"OK","correlationId":"manual-1"}')
    row = service.db.get_command("manual-1")
    assert row["origin"] == "external" and row["result"] == "OK"


def test_wall_button_is_logged_and_audited(service):
    button_on = {"result": "OK", "correlationId": "b42", "type": "on", "target": "all",
                 "value": 255, "source": "BUTTON"}
    service.ingest(topic(service, "ack"), json.dumps(button_on).encode())
    row = service.db.get_command("b42")
    assert (row["origin"], row["source"], row["cmd_type"]) == ("hub", "BUTTON", "on")
    # The button is a human command too: automation must not undo it.
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED", 0, 0))
    assert service.metrics(0)["override_violations"] == 1
    # A long press (resume) hands control back without a false alarm.
    resume = {**button_on, "correlationId": "b43", "type": "resume", "value": 0}
    service.ingest(topic(service, "ack"), json.dumps(resume).encode())
    service.ingest(topic(service, "telemetry"), telemetry("VACANT", 0, 0))
    assert service.metrics(0)["override_violations"] == 1


def test_lux_stored_and_in_history(service):
    service.ingest(topic(service, "telemetry"), telemetry("OCCUPIED_DAYLIGHT", ambient=900, lux=812.5))
    assert service.db.latest_telemetry(service.device_id)["lux"] == 812.5
    assert service.history(5)[0][3] == 812.5
    assert service.snapshot()["daylight_lux"] == {"on": 500, "off": 300}


def test_schedule_fires_when_due(service, clock):
    fake = FakeHubClient(service)
    service.attach_client(fake)
    sid = service.schedule_command(clock.now + 60, "scene", "all", scene="movie")
    service.run_due_schedules()
    assert not fake.published
    clock.advance(61)
    service.run_due_schedules()
    assert fake.published[0][1]["value"] == 64
    assert not service.db.pending_schedules()
    with pytest.raises(CommandError):
        service.schedule_command(clock.now - 1, "on", "all")
    assert not service.cancel_schedule(sid)
