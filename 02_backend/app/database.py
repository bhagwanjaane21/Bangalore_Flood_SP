"""SQLite storage; each operation explicitly closes its connection."""

import sqlite3
import math
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .telemetry import TelemetryConflict, TelemetryReading, utc_timestamp
from .alert_engine import (
    AlertPolicy, LEVEL_RANK, RULE_VERSION, SENSOR_RULES, data_freshness, evaluate_reading,
)


class TelemetryDatabase:
    def __init__(self, path: Path, policy: AlertPolicy | None = None):
        self.path = path
        self.policy = policy if policy is not None else AlertPolicy()

    @contextmanager
    def connect(self, *, create: bool = False):
        # Only initialization may create a DB. Later access must fail visibly
        # if storage disappears instead of creating an empty replacement.
        target = str(self.path) if create else f"{self.path.as_uri()}?mode=rw"
        connection = sqlite3.connect(target, uri=not create, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def init_db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect(create=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    water_depth_m REAL NOT NULL,
                    rate_of_rise_cm_min REAL DEFAULT 0.0,
                    battery_voltage REAL DEFAULT 3.7,
                    timestamp TEXT NOT NULL
                )
            """)
            # Check the original schema before attempting a non-destructive migration.
            connection.execute("""
                SELECT id, node_id, water_depth_m, rate_of_rise_cm_min,
                       battery_voltage, timestamp FROM telemetry LIMIT 0
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(telemetry)")}
            for name, column_type in {
                "observed_at": "TEXT", "received_at": "TEXT", "source": "TEXT",
                "timestamp_source": "TEXT", "reported_rate_of_rise_cm_min": "REAL",
                "rate_source": "TEXT",
                "alert_level": "TEXT", "alert_reason": "TEXT", "alert_kind": "TEXT",
                "alert_rule_version": "TEXT", "alert_recovery_count": "INTEGER",
                "alert_trend_rate_cm_min": "REAL", "alert_trend_samples": "INTEGER",
                "alert_quality_flags": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE telemetry ADD COLUMN {name} {column_type}")
            # Legacy naive timestamps have unknown timezone; do not invent UTC times.
            connection.execute("""
                UPDATE telemetry SET reported_rate_of_rise_cm_min = rate_of_rise_cm_min
                WHERE observed_at IS NULL AND reported_rate_of_rise_cm_min IS NULL
            """)
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS telemetry_node_observed
                ON telemetry(node_id, observed_at)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS alert_state (
                    node_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    recovery_count INTEGER NOT NULL,
                    reading_id INTEGER NOT NULL REFERENCES telemetry(id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    reading_id INTEGER NOT NULL UNIQUE REFERENCES telemetry(id),
                    previous_level TEXT NOT NULL,
                    new_level TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    observed_at TEXT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    data_status_at_event TEXT NOT NULL,
                    rule_version TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS alert_events_node_id ON alert_events(node_id, id)")
            # Bootstrap the latest historical decision without manufacturing past
            # notifications or replaying events on each application restart.
            for row in connection.execute("""
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY node_id
                        ORDER BY observed_at IS NOT NULL DESC, observed_at DESC, id DESC
                    ) AS position FROM telemetry
                ) WHERE position = 1
            """).fetchall():
                if row["node_id"] not in SENSOR_RULES:
                    continue
                state = connection.execute("SELECT * FROM alert_state WHERE node_id = ?", (row["node_id"],)).fetchone()
                if state and state["reading_id"] == row["id"] and row["alert_level"] is not None:
                    continue
                self._decide(connection, dict(row), datetime.now(timezone.utc), emit_event=False)
        self.check_ready()

    def check_ready(self):
        with self.connect() as connection:
            connection.execute("""
                SELECT id, node_id, water_depth_m, rate_of_rise_cm_min,
                       battery_voltage, timestamp, observed_at, received_at, source,
                       timestamp_source, reported_rate_of_rise_cm_min, rate_source,
                       alert_level, alert_reason, alert_kind, alert_rule_version,
                       alert_recovery_count, alert_trend_rate_cm_min, alert_trend_samples,
                       alert_quality_flags
                FROM telemetry LIMIT 0
            """)
            connection.execute("SELECT node_id, level, recovery_count, reading_id FROM alert_state LIMIT 0")
            connection.execute("SELECT id, reading_id, new_level, rule_version FROM alert_events LIMIT 0")

    def store_telemetry(self, reading: TelemetryReading, received_at: datetime) -> tuple[dict, bool]:
        observed_at = utc_timestamp(reading.observed_at or received_at)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("""
                SELECT * FROM telemetry WHERE node_id = ? AND observed_at = ?
            """, (reading.node_id, observed_at)).fetchone()
            if existing:
                if all(existing[key] == value for key, value in {
                    "water_depth_m": reading.water_depth_m,
                    "reported_rate_of_rise_cm_min": reading.rate_of_rise_cm_min,
                    "battery_voltage": reading.battery_voltage,
                    "source": reading.source,
                }.items()):
                    return dict(existing), False
                raise TelemetryConflict("Conflicting readings at the same observation time")
            previous = connection.execute("""
                SELECT * FROM telemetry WHERE node_id = ?
                ORDER BY observed_at IS NOT NULL DESC, observed_at DESC, id DESC LIMIT 1
            """, (reading.node_id,)).fetchone()
            if previous and previous["observed_at"]:
                if observed_at < previous["observed_at"]:
                    raise TelemetryConflict("Out-of-order observation; newer data already exists")
            rate = reading.rate_of_rise_cm_min
            rate_source = "reported" if rate is not None else "unavailable"
            if previous and previous["observed_at"]:
                elapsed = (datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                           - datetime.fromisoformat(previous["observed_at"].replace("Z", "+00:00"))).total_seconds()
                rate = (reading.water_depth_m - previous["water_depth_m"]) / elapsed * 6000
                if not math.isfinite(rate):
                    raise ValueError("Calculated rise rate exceeds the supported numeric range")
                rate = round(rate, 6)
                rate_source = "calculated"
            cursor = connection.execute("""
                INSERT INTO telemetry
                    (node_id, water_depth_m, rate_of_rise_cm_min, battery_voltage, timestamp,
                     observed_at, received_at, source, timestamp_source,
                     reported_rate_of_rise_cm_min, rate_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (reading.node_id, reading.water_depth_m, rate, reading.battery_voltage,
                  observed_at, observed_at, utc_timestamp(received_at), reading.source,
                  "client_observed" if reading.observed_at else "server_received",
                  reading.rate_of_rise_cm_min, rate_source))
            row = connection.execute("SELECT * FROM telemetry WHERE id = ?", (cursor.lastrowid,)).fetchone()
            self._decide(connection, dict(row), received_at)
            row = connection.execute("SELECT * FROM telemetry WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row), True

    def _decide(self, connection, reading: dict, now: datetime, *, emit_event: bool = True):
        state_row = connection.execute("SELECT * FROM alert_state WHERE node_id = ?", (reading["node_id"],)).fetchone()
        state = dict(state_row) if state_row else None
        rows = connection.execute("""
            SELECT * FROM telemetry WHERE node_id = ?
            ORDER BY observed_at IS NOT NULL DESC, observed_at DESC, id DESC LIMIT ?
        """, (reading["node_id"], self.policy.window_samples)).fetchall()
        history = [dict(row) for row in reversed(rows)]
        decision = evaluate_reading(reading, history, state, now, self.policy)
        connection.execute("""
            UPDATE telemetry SET alert_level = ?, alert_reason = ?, alert_kind = ?,
                alert_rule_version = ?, alert_recovery_count = ?, alert_trend_rate_cm_min = ?,
                alert_trend_samples = ?, alert_quality_flags = ? WHERE id = ?
        """, (decision.level, decision.reason, decision.kind, RULE_VERSION, decision.recovery_count,
              decision.trend_rate_cm_min, decision.trend_samples, json.dumps(decision.quality_flags), reading["id"]))
        connection.execute("""
            INSERT INTO alert_state (node_id, level, recovery_count, reading_id) VALUES (?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET level = excluded.level,
                recovery_count = excluded.recovery_count, reading_id = excluded.reading_id
        """, (reading["node_id"], decision.level, decision.recovery_count, reading["id"]))
        previous_level = state["level"] if state else "GREEN"
        if emit_event and decision.level != previous_level:
            event_type = "escalation" if LEVEL_RANK[decision.level] > LEVEL_RANK[previous_level] else "recovery"
            connection.execute("""
                INSERT INTO alert_events (node_id, reading_id, previous_level, new_level,
                    reason, event_type, observed_at, created_at, source, data_status_at_event, rule_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (reading["node_id"], reading["id"], previous_level, decision.level,
                  decision.reason, event_type, reading["observed_at"], utc_timestamp(now),
                  reading["source"] or "legacy", data_freshness(reading, now, self.policy.max_gap_seconds)["data_status"], RULE_VERSION))

    def get_latest_telemetry(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY node_id
                        ORDER BY observed_at IS NOT NULL DESC, observed_at DESC, id DESC
                    ) AS position FROM telemetry
                ) WHERE position = 1 ORDER BY node_id
            """).fetchall()
        return [dict(row) for row in rows]

    def get_history(self, node_id: str, limit: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM telemetry WHERE node_id = ?
                ORDER BY observed_at IS NOT NULL DESC, observed_at DESC, id DESC LIMIT ?
            """, (node_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_alerts(self, limit: int, node_id: str | None = None, after_id: int | None = None) -> list[dict]:
        clauses = []
        parameters = []
        if node_id is not None:
            clauses.append("e.node_id = ?")
            parameters.append(node_id)
        if after_id is not None:
            clauses.append("e.id > ?")
            parameters.append(after_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        order = "ASC" if after_id is not None else "DESC"
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(f"""
                SELECT e.*, t.water_depth_m, t.rate_of_rise_cm_min FROM alert_events e
                JOIN telemetry t ON t.id = e.reading_id
                {where} ORDER BY e.id {order} LIMIT ?
            """, parameters).fetchall()
        events = [dict(row) for row in rows]
        if after_id is None:
            events.reverse()
        return [{"event_id": event.pop("id"), **event} for event in events]
