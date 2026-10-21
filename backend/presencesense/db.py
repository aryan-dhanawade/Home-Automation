"""SQLite persistence. One connection per thread (MQTT loop, scheduler, web workers)."""

import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY,
    device_id        TEXT    NOT NULL,
    boot             INTEGER NOT NULL,
    seq_num          INTEGER NOT NULL,
    state            TEXT    NOT NULL,
    device_uptime_ms INTEGER NOT NULL,
    server_ts        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(server_ts);

CREATE TABLE IF NOT EXISTS telemetry (
    id               INTEGER PRIMARY KEY,
    device_id        TEXT    NOT NULL,
    state            TEXT    NOT NULL,
    brightness_a     INTEGER NOT NULL,
    brightness_b     INTEGER NOT NULL,
    ambient_raw      INTEGER NOT NULL,
    lux              REAL,
    distance_cm      REAL,
    fault            INTEGER,
    device_uptime_ms INTEGER NOT NULL,
    server_ts        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_ts ON telemetry(server_ts);

CREATE TABLE IF NOT EXISTS commands (
    id             INTEGER PRIMARY KEY,
    correlation_id TEXT UNIQUE NOT NULL,
    cmd_type       TEXT,
    target         TEXT,
    value          INTEGER,
    label          TEXT,
    source         TEXT NOT NULL,   -- transport: MQTT | REST
    origin         TEXT NOT NULL,   -- dashboard | schedule | hub (wall button) | external
    sent_at        REAL,
    acked_at       REAL,
    result         TEXT,
    rtt_ms         REAL
);
CREATE INDEX IF NOT EXISTS commands_sent ON commands(sent_at);

CREATE TABLE IF NOT EXISTS device_status (
    device_id TEXT PRIMARY KEY,
    online    INTEGER NOT NULL,
    last_seen REAL,
    changed   REAL
);

CREATE TABLE IF NOT EXISTS seq_gaps (
    id        INTEGER PRIMARY KEY,
    device_id TEXT    NOT NULL,
    boot      INTEGER NOT NULL,
    after_seq INTEGER NOT NULL,
    missing   INTEGER NOT NULL,
    server_ts REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    device_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT,
    raised_ts  REAL NOT NULL,
    cleared_ts REAL
);

CREATE TABLE IF NOT EXISTS rejected_messages (
    id        INTEGER PRIMARY KEY,
    topic     TEXT,
    reason    TEXT NOT NULL,
    excerpt   TEXT,
    server_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_rejections (
    id          INTEGER PRIMARY KEY,
    remote_addr TEXT,
    path        TEXT,
    server_ts   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_commands (
    id             INTEGER PRIMARY KEY,
    due_ts         REAL NOT NULL,
    cmd_type       TEXT NOT NULL,
    target         TEXT NOT NULL,
    value          INTEGER NOT NULL,
    label          TEXT,
    transport      TEXT NOT NULL,
    created_ts     REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | cancelled | failed
    correlation_id TEXT
);

-- Retention: raw rows older than the retention window collapse into these.
CREATE TABLE IF NOT EXISTS event_daily (
    day       TEXT NOT NULL,
    device_id TEXT NOT NULL,
    state     TEXT NOT NULL,
    count     INTEGER NOT NULL,
    PRIMARY KEY (day, device_id, state)
);

CREATE TABLE IF NOT EXISTS telemetry_daily (
    day          TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    samples      INTEGER NOT NULL,
    covered_s    REAL NOT NULL,
    lamp_a_full_s REAL NOT NULL,  -- seconds at full brightness equivalent
    lamp_b_full_s REAL NOT NULL,
    PRIMARY KEY (day, device_id)
);
"""


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        self.conn().executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        # Databases created before the ESP32 move have no lux column.
        columns = {r["name"] for r in self.conn().execute("PRAGMA table_info(telemetry)")}
        if "lux" not in columns:
            self.conn().execute("ALTER TABLE telemetry ADD COLUMN lux REAL")

    def conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self):
        conn = self.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _all(self, sql, params=()):
        return [dict(r) for r in self.conn().execute(sql, params)]

    def _one(self, sql, params=()):
        row = self.conn().execute(sql, params).fetchone()
        return dict(row) if row else None

    # --- events --------------------------------------------------------------

    def insert_event(self, device_id, boot, seq, state, uptime_ms, ts):
        self.conn().execute(
            "INSERT INTO events (device_id, boot, seq_num, state, device_uptime_ms, server_ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, boot, seq, state, uptime_ms, ts),
        )

    def last_event(self, device_id):
        return self._one(
            "SELECT boot, seq_num, device_uptime_ms FROM events WHERE device_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (device_id,),
        )

    def recent_events(self, device_id, limit=25):
        return self._all(
            "SELECT seq_num, state, device_uptime_ms, server_ts FROM events"
            " WHERE device_id = ? ORDER BY id DESC LIMIT ?",
            (device_id, limit),
        )

    def add_gap(self, device_id, boot, after_seq, missing, ts):
        self.conn().execute(
            "INSERT INTO seq_gaps (device_id, boot, after_seq, missing, server_ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (device_id, boot, after_seq, missing, ts),
        )

    # --- telemetry -----------------------------------------------------------

    def insert_telemetry(self, device_id, t, ts):
        self.conn().execute(
            "INSERT INTO telemetry (device_id, state, brightness_a, brightness_b, ambient_raw, lux,"
            " distance_cm, fault, device_uptime_ms, server_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                t["state"],
                t["lampA"],
                t["lampB"],
                t["ambient"],
                t["lux"],
                t["distance"],
                None if t["fault"] is None else int(t["fault"]),
                t["uptime"],
                ts,
            ),
        )

    def latest_telemetry(self, device_id):
        return self._one(
            "SELECT * FROM telemetry WHERE device_id = ? ORDER BY id DESC LIMIT 1", (device_id,)
        )

    def telemetry_between(self, device_id, since, until):
        return self._all(
            "SELECT server_ts, state, brightness_a, brightness_b, ambient_raw, lux, distance_cm"
            " FROM telemetry WHERE device_id = ? AND server_ts >= ? AND server_ts <= ?"
            " ORDER BY server_ts",
            (device_id, since, until),
        )

    def telemetry_daily_between(self, device_id, first_day, last_day):
        return self._all(
            "SELECT * FROM telemetry_daily WHERE device_id = ? AND day >= ? AND day <= ?"
            " ORDER BY day",
            (device_id, first_day, last_day),
        )

    # --- commands ------------------------------------------------------------

    def insert_command(self, cid, cmd_type, target, value, label, source, origin, sent_at):
        self.conn().execute(
            "INSERT INTO commands (correlation_id, cmd_type, target, value, label, source,"
            " origin, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, cmd_type, target, value, label, source, origin, sent_at),
        )

    def ack_command(self, cid, result, acked_at, rtt_ms=None):
        """Record an ack. Returns the command row, or None if the id is unknown."""
        conn = self.conn()
        if rtt_ms is None:
            conn.execute(
                "UPDATE commands SET result = ?, acked_at = ?,"
                " rtt_ms = CASE WHEN sent_at IS NULL THEN NULL ELSE (? - sent_at) * 1000 END"
                " WHERE correlation_id = ? AND acked_at IS NULL",
                (result, acked_at, acked_at, cid),
            )
        else:
            conn.execute(
                "UPDATE commands SET result = ?, acked_at = ?, rtt_ms = ?"
                " WHERE correlation_id = ? AND acked_at IS NULL",
                (result, acked_at, rtt_ms, cid),
            )
        return self.get_command(cid)

    def fail_command(self, cid, result):
        self.conn().execute(
            "UPDATE commands SET result = ? WHERE correlation_id = ? AND result IS NULL",
            (result, cid),
        )

    def get_command(self, cid):
        return self._one("SELECT * FROM commands WHERE correlation_id = ?", (cid,))

    def recent_commands(self, limit=15):
        return self._all("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))

    # --- device status -------------------------------------------------------

    def set_device_online(self, device_id, online, ts):
        self.conn().execute(
            "INSERT INTO device_status (device_id, online, last_seen, changed) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET"
            "   changed = CASE WHEN online != excluded.online THEN excluded.changed ELSE changed END,"
            "   online = excluded.online,"
            "   last_seen = CASE WHEN excluded.online THEN excluded.last_seen ELSE last_seen END",
            (device_id, int(online), ts, ts),
        )

    def touch_device(self, device_id, ts):
        self.conn().execute(
            "INSERT INTO device_status (device_id, online, last_seen, changed) VALUES (?, 1, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen",
            (device_id, ts, ts),
        )

    def device_status(self, device_id):
        return self._one("SELECT * FROM device_status WHERE device_id = ?", (device_id,))

    # --- alerts / security log -----------------------------------------------

    def active_alert(self, device_id, kind):
        return self._one(
            "SELECT * FROM alerts WHERE device_id = ? AND kind = ? AND cleared_ts IS NULL",
            (device_id, kind),
        )

    def raise_alert(self, device_id, kind, detail, ts):
        self.conn().execute(
            "INSERT INTO alerts (device_id, kind, detail, raised_ts) VALUES (?, ?, ?, ?)",
            (device_id, kind, detail, ts),
        )

    def clear_alert(self, device_id, kind, ts):
        self.conn().execute(
            "UPDATE alerts SET cleared_ts = ? WHERE device_id = ? AND kind = ? AND cleared_ts IS NULL",
            (ts, device_id, kind),
        )

    def recent_alerts(self, device_id, limit=10):
        return self._all(
            "SELECT * FROM alerts WHERE device_id = ?"
            " ORDER BY cleared_ts IS NOT NULL, raised_ts DESC LIMIT ?",
            (device_id, limit),
        )

    def log_rejected(self, topic, reason, excerpt, ts):
        self.conn().execute(
            "INSERT INTO rejected_messages (topic, reason, excerpt, server_ts) VALUES (?, ?, ?, ?)",
            (topic, reason, excerpt, ts),
        )

    def log_auth_rejection(self, remote_addr, path, ts):
        self.conn().execute(
            "INSERT INTO auth_rejections (remote_addr, path, server_ts) VALUES (?, ?, ?)",
            (remote_addr, path, ts),
        )

    # --- schedules -----------------------------------------------------------

    def add_schedule(self, due_ts, cmd_type, target, value, label, transport, now):
        cur = self.conn().execute(
            "INSERT INTO scheduled_commands (due_ts, cmd_type, target, value, label, transport,"
            " created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (due_ts, cmd_type, target, value, label, transport, now),
        )
        return cur.lastrowid

    def pending_schedules(self):
        return self._all(
            "SELECT * FROM scheduled_commands WHERE status = 'pending' ORDER BY due_ts"
        )

    def due_schedules(self, now):
        return self._all(
            "SELECT * FROM scheduled_commands WHERE status = 'pending' AND due_ts <= ?"
            " ORDER BY due_ts",
            (now,),
        )

    def mark_schedule(self, schedule_id, status, cid=None):
        cur = self.conn().execute(
            "UPDATE scheduled_commands SET status = ?, correlation_id = ?"
            " WHERE id = ? AND status = 'pending'",
            (status, cid, schedule_id),
        )
        return cur.rowcount > 0

    # --- metrics -------------------------------------------------------------

    def delivery_counts(self, device_id, since):
        received = self.conn().execute(
            "SELECT COUNT(*) FROM events WHERE device_id = ? AND server_ts >= ?", (device_id, since)
        ).fetchone()[0]
        missing = self.conn().execute(
            "SELECT COALESCE(SUM(missing), 0) FROM seq_gaps WHERE device_id = ? AND server_ts >= ?",
            (device_id, since),
        ).fetchone()[0]
        return received, missing

    def commands_since(self, since):
        return self._all(
            "SELECT * FROM commands WHERE sent_at >= ? OR (sent_at IS NULL AND acked_at >= ?)",
            (since, since),
        )

    def count(self, table, since, ts_column="server_ts", where="", params=()):
        sql = f"SELECT COUNT(*) FROM {table} WHERE {ts_column} >= ?" + (f" AND {where}" if where else "")
        return self.conn().execute(sql, (since, *params)).fetchone()[0]
