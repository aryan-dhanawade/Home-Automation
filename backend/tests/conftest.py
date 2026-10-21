import json
import threading
import time

import pytest

from presencesense.config import Settings
from presencesense.db import Database
from presencesense.service import HubService

TOKEN = "test-token-0123456789"


class FakeClock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeHubClient:
    """Stands in for paho: every publish to .../cmd is answered with an ack, as the hub would."""

    def __init__(self, service, result="OK", delay=0.0, respond=True):
        self.service = service
        self.result = result
        self.delay = delay
        self.respond = respond
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))
        if not self.respond:
            return
        cmd = json.loads(payload)
        ack = json.dumps({"result": self.result, "correlationId": cmd["correlationId"]}).encode()

        def reply():
            time.sleep(self.delay)
            self.service.ingest(self.service.settings.topic("ack"), ack)

        threading.Thread(target=reply, daemon=True).start()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        api_token=TOKEN,
        db_path=str(tmp_path / "test.db"),
        stuck_window_s=60,
        command_timeout_s=1.0,
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(settings, clock):
    return HubService(settings, Database(settings.db_path), clock=clock)


def telemetry(state="VACANT", a=0, b=0, ambient=300, uptime=1000, **extra):
    return json.dumps(
        {"state": state, "lampA": a, "lampB": b, "ambient": ambient, "uptime": uptime, **extra}
    ).encode()


def event(seq, state="OCCUPIED", uptime=1000):
    return json.dumps({"seq": seq, "state": state, "uptime": uptime}).encode()
