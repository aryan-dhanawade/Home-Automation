"""Rule engine: pure state machines fed by the ingest path, no I/O.

- SequenceTracker   event sequence gaps (delivery-reliability metric) and reboots
- StuckStateDetector "box in the beam": occupied for a long time with no movement
- OverrideAuditor   Objective 7, automation must never override a manual command
"""

import statistics
from collections import deque
from dataclasses import dataclass

from .protocol import OCCUPIED_STATES


@dataclass
class SeqObservation:
    boot: int
    missing: int = 0
    duplicate: bool = False
    rebooted: bool = False


class SequenceTracker:
    """The hub numbers events 1, 2, 3... from boot; uptime is millis() since boot."""

    def __init__(self, last_seq=None, last_uptime=None, boot=0):
        self.last_seq = last_seq
        self.last_uptime = last_uptime
        self.boot = boot

    def observe(self, seq, uptime):
        obs = SeqObservation(boot=self.boot)
        if self.last_seq is None:
            # First event we have ever seen: we were not listening before it,
            # so nothing earlier counts as lost.
            pass
        elif seq == self.last_seq + 1:
            pass  # in order (also covers millis() wrapping at ~49 days)
        elif uptime < self.last_uptime:
            self.boot += 1
            obs = SeqObservation(boot=self.boot, rebooted=True, missing=seq - 1)
        elif seq > self.last_seq + 1:
            obs.missing = seq - self.last_seq - 1
        else:
            obs.duplicate = True
            return obs
        self.last_seq = seq
        self.last_uptime = uptime
        return obs


class StuckStateDetector:
    """A person fidgets; a box parked in the beam returns the same range forever.

    Raises when the hub has been continuously occupied for `window_s` and the
    reported distance varied by no more than `ceiling_cm` (population stdev) over
    that window. Without distance in telemetry it can only fall back to duration.
    """

    def __init__(self, window_s, ceiling_cm, duration_only_s):
        self.window_s = window_s
        self.ceiling_cm = ceiling_cm
        self.duration_only_s = duration_only_s
        self._run_start = None
        self._samples = deque()
        self.flagged = False

    def observe(self, ts, state, distance):
        """Returns ('raise', detail), ('clear', None) or None."""
        if state not in OCCUPIED_STATES:
            self._run_start = None
            self._samples.clear()
            if self.flagged:
                self.flagged = False
                return ("clear", None)
            return None

        if self._run_start is None:
            self._run_start = ts
        if distance is not None and distance >= 0:
            self._samples.append((ts, distance))
        while self._samples and self._samples[0][0] < ts - self.window_s:
            self._samples.popleft()

        if self.flagged:
            return None
        occupied_for = ts - self._run_start
        if occupied_for < self.window_s:
            return None

        distances = [d for _, d in self._samples]
        if len(distances) >= 3:
            spread = statistics.pstdev(distances)
            if spread <= self.ceiling_cm:
                self.flagged = True
                return (
                    "raise",
                    f"occupied {occupied_for / 60:.0f} min with distance stdev "
                    f"{spread:.2f} cm (ceiling {self.ceiling_cm} cm): object in beam?",
                )
        elif occupied_for >= self.duration_only_s:
            self.flagged = True
            return ("raise", f"occupied {occupied_for / 3600:.1f} h with no distance data")
        return None


class OverrideAuditor:
    """After an acknowledged manual command the hub must report OVERRIDE, with the
    commanded lamp levels, until an acknowledged `resume`. Anything else is a
    violation of Objective 7.

    Only lamps that were explicitly commanded are checked: the hub freezes the
    other lamp wherever it was, which the backend cannot know exactly.
    """

    def __init__(self):
        self.expected = None  # None: automation is allowed to drive the lamps

    def on_ack(self, cmd_type, target, value):
        if cmd_type == "resume":
            self.expected = None
            return
        lamps = ("lampA", "lampB") if target == "all" else (target,)
        self.expected = dict(self.expected or {})
        self.expected.update({lamp: value for lamp in lamps})

    def on_reboot(self):
        # A reboot clears the hub's override flag; that is a documented limitation,
        # not automation overriding a human.
        self.expected = None

    def check(self, telemetry):
        """Returns a violation description, or None."""
        if self.expected is None:
            return None
        if telemetry["state"] != "OVERRIDE":
            return f"state {telemetry['state']} while a manual override is in force"
        for lamp, level in self.expected.items():
            if telemetry[lamp] != level:
                return f"{lamp} at {telemetry[lamp]}, commanded {level}"
        return None
