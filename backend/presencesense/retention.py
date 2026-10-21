"""Data minimisation: raw events/telemetry older than the retention window are
collapsed into daily totals and the raw rows deleted. Only whole local days
older than the cutoff are aggregated, so a day is never split across tables.
"""

from collections import defaultdict
from datetime import datetime, time as dtime

from .energy import integrate


def _day_start(ts):
    return datetime.combine(datetime.fromtimestamp(ts).date(), dtime.min).timestamp()


def run_retention(db, now, days, max_gap_s, hold_s):
    cutoff = _day_start(now - days * 86400)
    with db.transaction() as conn:
        event_rows = conn.execute(
            "SELECT date(server_ts, 'unixepoch', 'localtime') AS day, device_id, state,"
            " COUNT(*) AS n FROM events WHERE server_ts < ? GROUP BY day, device_id, state",
            (cutoff,),
        ).fetchall()
        for r in event_rows:
            conn.execute(
                "INSERT INTO event_daily (day, device_id, state, count) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(day, device_id, state) DO UPDATE SET count = count + excluded.count",
                (r["day"], r["device_id"], r["state"], r["n"]),
            )
        events_deleted = conn.execute("DELETE FROM events WHERE server_ts < ?", (cutoff,)).rowcount

        telemetry = conn.execute(
            "SELECT device_id, server_ts, brightness_a, brightness_b FROM telemetry"
            " WHERE server_ts < ? ORDER BY device_id, server_ts",
            (cutoff,),
        ).fetchall()
        per_day = defaultdict(list)
        for r in telemetry:
            day = datetime.fromtimestamp(r["server_ts"]).date().isoformat()
            per_day[(day, r["device_id"])].append(
                (r["server_ts"], r["brightness_a"], r["brightness_b"])
            )
        for (day, device_id), samples in per_day.items():
            start = datetime.fromisoformat(day).timestamp()
            covered, full_a, full_b = integrate(samples, start, start + 86400, max_gap_s, hold_s)
            conn.execute(
                "INSERT INTO telemetry_daily (day, device_id, samples, covered_s, lamp_a_full_s,"
                " lamp_b_full_s) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(day, device_id) DO UPDATE SET samples = samples + excluded.samples,"
                " covered_s = covered_s + excluded.covered_s,"
                " lamp_a_full_s = lamp_a_full_s + excluded.lamp_a_full_s,"
                " lamp_b_full_s = lamp_b_full_s + excluded.lamp_b_full_s",
                (day, device_id, len(samples), covered, full_a, full_b),
            )
        telemetry_deleted = conn.execute(
            "DELETE FROM telemetry WHERE server_ts < ?", (cutoff,)
        ).rowcount

    return {
        "cutoff": cutoff,
        "events_aggregated": events_deleted,
        "telemetry_aggregated": telemetry_deleted,
    }
