"""SQLite storage; each operation explicitly closes its connection."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


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
        self.check_ready()

    def check_ready(self):
        with self.connect() as connection:
            connection.execute("""
                SELECT id, node_id, water_depth_m, rate_of_rise_cm_min,
                       battery_voltage, timestamp FROM telemetry LIMIT 0
            """)

    def log_telemetry(self, node_id: str, depth: float,
                      rise_rate: float = 0.0, voltage: float = 3.7):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO telemetry
                    (node_id, water_depth_m, rate_of_rise_cm_min, battery_voltage, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (node_id, depth, rise_rate, voltage,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    def get_latest_telemetry(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT t1.* FROM telemetry t1
                INNER JOIN (
                    SELECT node_id, MAX(id) AS max_id FROM telemetry GROUP BY node_id
                ) t2 ON t1.id = t2.max_id
            """).fetchall()
        return [dict(row) for row in rows]
