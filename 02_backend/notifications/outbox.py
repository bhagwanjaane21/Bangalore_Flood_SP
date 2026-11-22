"""Transactional ingestion cursor and delivery ledger, separate from telemetry."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Outbox:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect(create=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS streams (
                scope TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0,
                blocked_until REAL NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                scope TEXT NOT NULL, event_id INTEGER NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                due_at REAL NOT NULL DEFAULT 0, claim TEXT, lease_until REAL,
                detail TEXT, message_id INTEGER,
                PRIMARY KEY (scope, event_id))""")

    @contextmanager
    def connect(self, *, create=False):
        target = str(self.path) if create else f"{self.path.as_uri()}?mode=rw"
        db = sqlite3.connect(target, uri=not create, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cursor(self, scope):
        with self.connect() as db:
            row = db.execute("SELECT cursor FROM streams WHERE scope = ?", (scope,)).fetchone()
            return row[0] if row else 0

    def enqueue(self, scope, events):
        # Cursor movement and every event in the batch commit or roll back together.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO streams(scope) VALUES (?)", (scope,))
            cursor = db.execute("SELECT cursor FROM streams WHERE scope = ?", (scope,)).fetchone()[0]
            previous = 0
            for event in events:
                event_id = event["event_id"]
                if type(event_id) is not int or event_id <= previous:
                    raise ValueError("Alert events must have positive, ascending IDs")
                previous = event_id
                db.execute("INSERT OR IGNORE INTO deliveries(scope,event_id,payload) VALUES (?,?,?)",
                           (scope, event_id, json.dumps(event, allow_nan=False)))
            db.execute("UPDATE streams SET cursor = ? WHERE scope = ?", (max(cursor, previous), scope))

    def claim(self, scope, now, *, live, max_attempts):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A process can die after Telegram accepts but before we save its reply.
            # Live deliveries in that window need operator review, not an automatic replay.
            db.execute("""UPDATE deliveries SET status = ?, detail = 'worker_interrupted', claim = NULL
                WHERE scope = ? AND status = 'sending' AND lease_until <= ?""",
                       ("uncertain" if live else "retry", scope, now))
            db.execute("""UPDATE deliveries SET status = 'failed', detail = 'attempt_limit'
                WHERE scope = ? AND status IN ('pending','retry') AND attempts >= ?""",
                       (scope, max_attempts))
            row = db.execute("""SELECT d.* FROM deliveries d JOIN streams s ON d.scope = s.scope
                WHERE d.scope = ? AND d.status IN ('pending','retry') AND d.due_at <= ?
                AND s.blocked_until <= ? ORDER BY d.event_id LIMIT 1""", (scope, now, now)).fetchone()
            if row is None:
                return None
            claim_id = str(uuid.uuid4())
            db.execute("""UPDATE deliveries SET status = 'sending', claim = ?, lease_until = ?
                WHERE scope = ? AND event_id = ?""", (claim_id, now + 120, scope, row["event_id"]))
            return {**dict(row), "claim": claim_id, "event": json.loads(row["payload"])}

    def start_attempt(self, scope, item):
        with self.connect() as db:
            result = db.execute("""UPDATE deliveries SET attempts = attempts + 1
                WHERE scope = ? AND event_id = ? AND status = 'sending' AND claim = ?""",
                (scope, item["event_id"], item["claim"]))
            if result.rowcount != 1:
                raise RuntimeError("Delivery claim lost")

    def finish(self, scope, item, status, detail, *, due_at=0, message_id=None, cooldown=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("""UPDATE deliveries SET status=?, detail=?, due_at=?, message_id=?,
                claim=NULL, lease_until=NULL WHERE scope=? AND event_id=? AND claim=? AND status='sending'""",
                (status, detail, due_at, message_id, scope, item["event_id"], item["claim"]))
            if result.rowcount != 1:
                raise RuntimeError("Delivery claim lost")
            if cooldown:
                db.execute("UPDATE streams SET blocked_until = MAX(blocked_until, ?) WHERE scope = ?",
                           (due_at, scope))

    def retry_uncertain(self, scope, event_id):
        with self.connect() as db:
            result = db.execute("""UPDATE deliveries SET status='retry', due_at=0,
                detail='operator_retry' WHERE scope=? AND event_id=? AND status='uncertain'""",
                (scope, event_id))
            if result.rowcount != 1:
                raise ValueError("No uncertain delivery with that event ID in the current stream/destination")

    def rows(self, scope=None):
        with self.connect() as db:
            if scope is None:
                rows = db.execute("SELECT * FROM deliveries ORDER BY scope,event_id").fetchall()
            else:
                rows = db.execute("SELECT * FROM deliveries WHERE scope=? ORDER BY event_id", (scope,)).fetchall()
            return [dict(row) for row in rows]
