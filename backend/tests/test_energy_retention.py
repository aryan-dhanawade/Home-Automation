import pytest

from presencesense.energy import compute_report, integrate
from presencesense.retention import run_retention

from .conftest import telemetry


def test_half_brightness_for_an_hour():
    samples = [(t, 255, 0) if (t // 60) % 2 == 0 else (t, 0, 0) for t in range(0, 3600, 5)]
    r = compute_report(samples, 0, 3600, (10.0, 10.0), 0.25, 15, 5)
    a, b = r.lamps
    assert r.covered_s == pytest.approx(3600)
    assert a.actual_wh == pytest.approx(5.0)
    assert a.baseline_wh == pytest.approx(10.0)
    assert b.actual_wh == 0
    assert r.hub_wh == pytest.approx(0.25)
    assert r.net_saved_wh == pytest.approx(20 - 5 - 0.25)
    assert not r.warnings


def test_gaps_excluded_from_both_totals():
    samples = [(0, 255, 255), (5, 255, 255), (600, 255, 255), (605, 255, 255)]
    covered, full_a, _ = integrate(samples, 0, 610, max_gap_s=15, hold_s=5)
    assert covered == pytest.approx(15)
    assert full_a == pytest.approx(15)


def test_short_window_warns():
    r = compute_report([(0, 0, 0)], 0, 600, (9, 9), 0.25, 15, 5)
    assert any("30 min" in w for w in r.warnings)


def test_retention_aggregates_old_rows_and_keeps_report(service, clock):
    old = clock.now - 40 * 86400
    for i in range(0, 720):  # one hour of telemetry at full brightness, 40 days ago
        service.ingest(service.settings.topic("telemetry"), telemetry("OCCUPIED", 255, 0, uptime=i), now=old + i * 5)
    service.ingest(service.settings.topic("event"), b'{"seq":1,"state":"OCCUPIED","uptime":1}', now=old)
    service.ingest(service.settings.topic("telemetry"), telemetry(), now=clock.now)

    before = service.energy_report(old - 60, old + 3600)
    result = service.run_retention()
    assert result["telemetry_aggregated"] == 720 and result["events_aggregated"] == 1

    conn = service.db.conn()
    assert conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 1
    assert conn.execute("SELECT count FROM event_daily").fetchone()[0] == 1

    after = service.energy_report(old - 60, old + 3600)
    assert after.lamps[0].actual_wh == pytest.approx(before.lamps[0].actual_wh, rel=0.01)
