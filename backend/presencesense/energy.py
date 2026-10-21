"""Modelled energy report: logged lamp duty vs an always-on baseline.

Lamp energy = rated W x sum(brightness/255 x dt), sample-and-hold between
telemetry snapshots. Intervals longer than `max_gap_s` (lost telemetry, hub
offline) are excluded from both the lamp total and the baseline, so the
comparison stays like-for-like. The hub's own draw is a separate line item.

This is a model, not a measurement (no INA219 on the hub); it is reported, not
targeted.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime

MIN_WINDOW_S = 30 * 60


@dataclass
class LampLine:
    name: str
    rated_w: float
    full_s: float  # full-brightness-equivalent seconds
    covered_s: float

    @property
    def actual_wh(self):
        return self.rated_w * self.full_s / 3600

    @property
    def baseline_wh(self):
        return self.rated_w * self.covered_s / 3600

    @property
    def duty_pct(self):
        return 100 * self.full_s / self.covered_s if self.covered_s else 0.0

    def as_dict(self):
        return {
            "name": self.name,
            "rated_w": self.rated_w,
            "actual_wh": round(self.actual_wh, 3),
            "baseline_wh": round(self.baseline_wh, 3),
            "saved_wh": round(self.baseline_wh - self.actual_wh, 3),
            "duty_pct": round(self.duty_pct, 1),
        }


@dataclass
class EnergyReport:
    since: float
    until: float
    samples: int
    covered_s: float
    unknown_s: float
    lamps: list
    hub_w: float
    warnings: list = field(default_factory=list)

    @property
    def lamps_actual_wh(self):
        return sum(l.actual_wh for l in self.lamps)

    @property
    def baseline_wh(self):
        return sum(l.baseline_wh for l in self.lamps)

    @property
    def hub_wh(self):
        return self.hub_w * self.covered_s / 3600

    @property
    def net_saved_wh(self):
        return self.baseline_wh - self.lamps_actual_wh - self.hub_wh

    @property
    def net_saved_pct(self):
        return 100 * self.net_saved_wh / self.baseline_wh if self.baseline_wh else 0.0

    def as_dict(self):
        return {
            "since": self.since,
            "until": self.until,
            "samples": self.samples,
            "covered_s": round(self.covered_s, 1),
            "unknown_s": round(self.unknown_s, 1),
            "lamps": [l.as_dict() for l in self.lamps],
            "lamps_actual_wh": round(self.lamps_actual_wh, 3),
            "baseline_wh": round(self.baseline_wh, 3),
            "hub_w": self.hub_w,
            "hub_wh": round(self.hub_wh, 3),
            "net_saved_wh": round(self.net_saved_wh, 3),
            "net_saved_pct": round(self.net_saved_pct, 1),
            "warnings": self.warnings,
        }

    def to_markdown(self):
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# PresenceSense energy report",
            "",
            f"Window: {fmt(self.since)} to {fmt(self.until)}  ",
            f"Telemetry samples: {self.samples}; covered {self.covered_s / 60:.1f} min, "
            f"unknown (gaps) {self.unknown_s / 60:.1f} min",
            "",
            "| Line item | Rated W | Duty | Actual Wh | Always-on Wh | Saved Wh |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for lamp in self.lamps:
            d = lamp.as_dict()
            lines.append(
                f"| {d['name']} | {d['rated_w']:g} | {d['duty_pct']:.1f} % | {d['actual_wh']:.2f}"
                f" | {d['baseline_wh']:.2f} | {d['saved_wh']:.2f} |"
            )
        lines += [
            f"| Hub (always on, separate item) | {self.hub_w:g} | 100 % | {self.hub_wh:.2f} | - | "
            f"-{self.hub_wh:.2f} |",
            f"| **Net** | | | **{self.lamps_actual_wh + self.hub_wh:.2f}** | "
            f"**{self.baseline_wh:.2f}** | **{self.net_saved_wh:.2f} ({self.net_saved_pct:.1f} %)** |",
            "",
            "Modelled from logged brightness (sample-and-hold between snapshots), not measured.",
        ]
        if self.warnings:
            lines += ["", *[f"> Warning: {w}" for w in self.warnings]]
        return "\n".join(lines) + "\n"


def integrate(samples, since, until, max_gap_s, hold_s):
    """Integrate (ts, brightness_a, brightness_b) samples over [since, until].

    Returns (covered_s, full_a_s, full_b_s). Each sample holds until the next
    one, or for `hold_s` after the last one; holds longer than `max_gap_s` are
    dropped entirely (unknown).
    """
    covered = full_a = full_b = 0.0
    for i, (ts, a, b) in enumerate(samples):
        end = samples[i + 1][0] if i + 1 < len(samples) else ts + hold_s
        if end - ts > max_gap_s:
            continue
        start, end = max(ts, since), min(end, until)
        if end <= start:
            continue
        dt = end - start
        covered += dt
        full_a += dt * a / 255
        full_b += dt * b / 255
    return covered, full_a, full_b


def compute_report(
    samples, since, until, lamp_watts, hub_w, max_gap_s, hold_s, daily_rows=()
):
    """`samples`: (ts, a, b) sorted by ts, including the last one before `since`.
    `daily_rows`: telemetry_daily rows (retention aggregates) inside the window.
    """
    covered, full_a, full_b = integrate(samples, since, until, max_gap_s, hold_s)
    for row in daily_rows:
        covered += row["covered_s"]
        full_a += row["lamp_a_full_s"]
        full_b += row["lamp_b_full_s"]

    span = until - since
    warnings = []
    if span < MIN_WINDOW_S:
        warnings.append(f"Window is {span / 60:.0f} min; the evaluation needs at least 30 min.")
    if span > 0 and covered < 0.9 * span:
        warnings.append(
            f"Telemetry covers only {100 * covered / span:.0f} % of the window; "
            "the rest is excluded from both totals."
        )
    if daily_rows:
        warnings.append(f"{len(daily_rows)} day(s) come from retention aggregates (day resolution).")

    in_window = sum(1 for ts, _, _ in samples if since <= ts <= until)
    return EnergyReport(
        since=since,
        until=until,
        samples=in_window + sum(r["samples"] for r in daily_rows),
        covered_s=covered,
        unknown_s=max(0.0, span - covered),
        lamps=[
            LampLine("Lamp A (living room)", lamp_watts[0], full_a, covered),
            LampLine("Lamp B (bedroom)", lamp_watts[1], full_b, covered),
        ],
        hub_w=hub_w,
        warnings=warnings,
    )
