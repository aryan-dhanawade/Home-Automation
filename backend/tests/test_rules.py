from presencesense.rules import OverrideAuditor, SequenceTracker, StuckStateDetector


def test_sequence_in_order_and_gap():
    t = SequenceTracker()
    assert t.observe(5, 100).missing == 0  # first ever: nothing counted as lost
    assert t.observe(6, 200).missing == 0
    obs = t.observe(9, 300)
    assert obs.missing == 2 and not obs.rebooted
    assert t.observe(9, 300).duplicate


def test_sequence_reboot_counts_missed_events_since_boot():
    t = SequenceTracker(last_seq=40, last_uptime=900_000, boot=2)
    obs = t.observe(3, 1_500)
    assert obs.rebooted and obs.boot == 3 and obs.missing == 2
    assert t.observe(4, 2_000).missing == 0


def test_sequence_millis_wrap_is_not_a_reboot():
    t = SequenceTracker(last_seq=10, last_uptime=2**32 - 100)
    obs = t.observe(11, 50)
    assert not obs.rebooted and obs.missing == 0


def test_stuck_state_raises_for_static_object_and_clears():
    d = StuckStateDetector(window_s=60, ceiling_cm=1.0, duration_only_s=3600)
    out = [d.observe(ts, "OCCUPIED", 70.0) for ts in range(0, 66, 5)]
    raised = [o for o in out if o]
    assert len(raised) == 1 and raised[0][0] == "raise"
    assert d.observe(70, "GRACE", 70.0) == ("clear", None)


def test_stuck_state_ignores_fidgeting_person():
    d = StuckStateDetector(window_s=60, ceiling_cm=1.0, duration_only_s=3600)
    out = [d.observe(ts, "OCCUPIED", 80 + (ts % 3) * 4) for ts in range(0, 300, 5)]
    assert not any(out)


def test_stuck_state_duration_fallback_without_distance():
    d = StuckStateDetector(window_s=60, ceiling_cm=1.0, duration_only_s=120)
    out = [d.observe(ts, "OCCUPIED_DAYLIGHT", None) for ts in range(0, 130, 5)]
    assert [o[0] for o in out if o] == ["raise"]


def test_override_auditor():
    a = OverrideAuditor()
    base = {"state": "OCCUPIED", "lampA": 255, "lampB": 255}
    assert a.check(base) is None  # automation free to run
    a.on_ack("brightness", "lampA", 40)
    assert "state OCCUPIED" in a.check(base)
    assert a.check({"state": "OVERRIDE", "lampA": 40, "lampB": 255}) is None
    assert "lampA" in a.check({"state": "OVERRIDE", "lampA": 255, "lampB": 255})
    a.on_ack("resume", "all", 0)
    assert a.check(base) is None
