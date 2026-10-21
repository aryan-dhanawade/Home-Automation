import pytest

from presencesense.web import create_app

from .conftest import TOKEN, FakeHubClient, telemetry

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(service):
    service.attach_client(FakeHubClient(service))
    return create_app(service).test_client()


def test_index_served_without_data(client):
    res = client.get("/")
    assert res.status_code == 200 and b"PresenceSense" in res.data


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}])
def test_api_requires_token_and_logs_rejections(client, service, headers):
    assert client.get("/api/snapshot", headers=headers).status_code == 401
    assert client.post("/api/command", json={"type": "on", "target": "all"}, headers=headers).status_code == 401
    assert service.db.conn().execute("SELECT COUNT(*) FROM auth_rejections").fetchone()[0] == 2


def test_snapshot_and_history(client, service):
    service.ingest(service.settings.topic("telemetry"), telemetry("OCCUPIED", 255, 128))
    snap = client.get("/api/snapshot", headers=AUTH).get_json()
    assert snap["telemetry"]["brightness_b"] == 128
    points = client.get("/api/history?minutes=bogus", headers=AUTH).get_json()["points"]
    assert len(points) == 1


def test_command_endpoint(client):
    res = client.post("/api/command", json={"type": "brightness", "target": "lampB", "value": 77}, headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["result"] == "OK"
    bad = client.post("/api/command", json={"type": "brightness", "target": "lampB", "value": 999}, headers=AUTH)
    assert bad.status_code == 400 and "0..255" in bad.get_json()["error"]
    assert client.post("/api/command", data="nope", headers=AUTH).status_code == 400


def test_schedule_endpoints(client, service, clock):
    res = client.post(
        "/api/schedule", json={"type": "on", "target": "all", "due_ts": clock.now + 120}, headers=AUTH
    )
    assert res.status_code == 201
    sid = res.get_json()["id"]
    assert client.delete(f"/api/schedule/{sid}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/schedule/{sid}", headers=AUTH).status_code == 404


def test_energy_and_metrics(client):
    assert "lamps" in client.get("/api/energy?minutes=30", headers=AUTH).get_json()
    md = client.get("/api/energy.md", headers=AUTH)
    assert md.mimetype == "text/markdown" and b"energy report" in md.data
    assert "delivery_pct" in client.get("/api/metrics", headers=AUTH).get_json()


def test_cors_locked_to_dashboard_origin(client, service):
    ok = client.get("/api/snapshot", headers={**AUTH, "Origin": service.settings.dashboard_origin})
    assert ok.headers.get("Access-Control-Allow-Origin") == service.settings.dashboard_origin
    other = client.get("/api/snapshot", headers={**AUTH, "Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in other.headers
