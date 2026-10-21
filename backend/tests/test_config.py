import pytest

from presencesense.__main__ import _require_private_topics
from presencesense.config import Settings
from presencesense.db import Database

BROKER_VARS = ("PS_MQTT_HOST", "PS_MQTT_PORT", "PS_MQTT_TRANSPORT", "PS_MQTT_TLS",
               "PS_MQTT_USERNAME", "PS_MQTT_PASSWORD", "PS_HUB_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in BROKER_VARS:
        monkeypatch.delenv(name, raising=False)


def test_default_is_shiftr_over_websocket_tls_443():
    s = Settings.from_env()
    assert (s.mqtt_host, s.mqtt_port, s.mqtt_transport, s.mqtt_tls) == (
        "public.cloud.shiftr.io", 443, "websockets", True)
    assert s.mqtt_username == "public"


def test_local_broker_means_plain_tcp(monkeypatch):
    monkeypatch.setenv("PS_MQTT_HOST", "127.0.0.1")
    s = Settings.from_env()
    assert (s.mqtt_port, s.mqtt_transport, s.mqtt_tls, s.mqtt_username) == (1883, "tcp", False, "")


def test_guessable_hub_id_refused_on_public_broker(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _require_private_topics(Settings.from_env())
    assert "PS_HUB_ID=ps-" in str(exc.value)
    monkeypatch.setenv("PS_HUB_ID", "ps-3f9a1c0b7d2e4a65")
    _require_private_topics(Settings.from_env())
    monkeypatch.setenv("PS_HUB_ID", "hub01")
    monkeypatch.setenv("PS_MQTT_HOST", "127.0.0.1")
    _require_private_topics(Settings.from_env())  # local broker: fine


def test_old_database_gets_lux_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE telemetry (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, state TEXT NOT NULL,"
        " brightness_a INTEGER NOT NULL, brightness_b INTEGER NOT NULL, ambient_raw INTEGER NOT NULL,"
        " distance_cm REAL, fault INTEGER, device_uptime_ms INTEGER NOT NULL, server_ts REAL NOT NULL)"
    )
    conn.close()
    db = Database(path)
    columns = {r["name"] for r in db.conn().execute("PRAGMA table_info(telemetry)")}
    assert "lux" in columns
