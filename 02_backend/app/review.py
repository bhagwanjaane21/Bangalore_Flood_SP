"""Read-only access to the current isolated rehearsal's recorded checkpoints."""

import json
import sqlite3
from contextlib import closing

from fastapi import HTTPException


def read_review(settings):
    run_id = settings.simulation_run_id
    if not run_id:
        return {"mode": "monitoring", "frames": []}
    path = settings.database_path.with_name("review.db")
    if not path.is_file():
        status = "not_recorded" if path.with_name("report.json").is_file() else "waiting"
        return {"mode": "simulation", "run_id": run_id, "status": status, "frames": []}
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute("SELECT document FROM review WHERE run_id = ?", (run_id,)).fetchone()
        if row is not None:
            return json.loads(row[0])
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(503, "Recorded checkpoints are temporarily unavailable") from exc
    raise HTTPException(409, "Recorded checkpoints belong to a different simulation")
