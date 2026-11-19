import sqlite3
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = "telemetry.db"

def init_db():
    """Initializes the SQLite database with the telemetry table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            water_depth_m REAL NOT NULL,
            rate_of_rise_cm_min REAL DEFAULT 0.0,
            battery_voltage REAL DEFAULT 3.7,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def log_telemetry(node_id: str, depth: float, rise_rate: float = 0.0, voltage: float = 3.7):
    """Inserts a new water depth reading with the active system timestamp."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO telemetry (node_id, water_depth_m, rate_of_rise_cm_min, battery_voltage, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (node_id, depth, rise_rate, voltage, current_time))
    conn.commit()
    conn.close()

def get_latest_telemetry() -> List[Dict[str, Any]]:
    """Retrieves the latest reading for each sensor node."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t1.* FROM telemetry t1
        INNER JOIN (
            SELECT node_id, MAX(id) as max_id
            FROM telemetry
            GROUP BY node_id
        ) t2 ON t1.id = t2.max_id
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

if __name__ == "__main__":
    init_db()
    print("Database tables initialized successfully.")