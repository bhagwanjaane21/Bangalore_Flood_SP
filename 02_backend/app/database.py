"""SQLite storage; each operation explicitly closes its connection."""

import sqlite3
import math
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .telemetry import TelemetryConflict, TelemetryReading, utc_timestamp


class TelemetryDatabase:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self, *, create: bool = False):
        # Only initialization may create a DB. Later access must fail visibly
        # if storage disappears instead of creating an empty replacement.
        target = str(self.path) if create else f"{self.path.as_uri()}?mode=rw"
        connection = sqlite3.connect(target, uri=not create, timeout=5)
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
        self.check_ready()

    def check_ready(self):
        with self.connect() as connection:
            connection.execute("""
                SELECT id, node_id, water_depth_m, rate_of_rise_cm_min,
                       battery_voltage, timestamp, observed_at, received_at, source,
                       timestamp_source, reported_rate_of_rise_cm_min, rate_source
                FROM telemetry LIMIT 0
            """)

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
        return dict(row), True

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
