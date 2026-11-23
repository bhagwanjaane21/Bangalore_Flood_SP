"""Capture verified API snapshots and actual local notification worker previews."""

import json

from notifications.outbox import Outbox
from notifications.worker import BackendClient, Worker, scope_for

from .runner import SimulationError
from .scenarios import NODES


class ReviewRecorder:
    def __init__(self, folder, run_id, scenario, base_url):
        self.ledger = Outbox(folder / "review.db")
        self.document = {
            "mode": "simulation", "run_id": run_id, "scenario": scenario.name,
            "description": scenario.description, "status": "running",
            "total_frames": len(scenario.steps) + int(bool(scenario.dropout_node)),
            "preview_count": 0, "frames": [],
        }
        with self.ledger.connect() as db:
            db.execute("CREATE TABLE review (run_id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        self.save()
        self.client = BackendClient(base_url)
        self.cards = []
        # A worker without a sender can only produce local previews.
        self.worker = Worker(self.client, self.ledger, emit=self.cards.append)

    def save(self):
        with self.ledger.connect() as db:
            db.execute("INSERT OR REPLACE INTO review(run_id, document) VALUES (?, ?)",
                       (self.document["run_id"], json.dumps(self.document, allow_nan=False)))

    def capture(self, checkpoint, snapshot):
        self.cards.clear()
        delivery = self.worker.cycle()
        rows = self.ledger.rows(scope_for(delivery["stream_id"]))
        previous_count = self.document["preview_count"]
        if any(row["status"] != "dry_run" for row in rows) or len(rows) != previous_count + len(self.cards):
            raise SimulationError("The dry-run ledger did not confirm a preview for every transition")
        previews = []
        for row, card in zip(rows[previous_count:], self.cards, strict=True):
            event = json.loads(row["payload"])
            previews.append({key: event[key] for key in ("event_id", "node_id", "previous_level", "new_level")}
                            | {"text": card})
        stations = [{key: snapshot[node][key] for key in (
            "node_id", "name", "water_depth_m", "observed_at", "source", "data_status",
            "alert_level", "alert_kind", "alert_reason",
        )} for node in NODES]
        self.document["frames"].append({
            "label": checkpoint["label"], "offset_seconds": checkpoint["offset_seconds"],
            "stations": stations, "previews": previews,
        })
        self.document["preview_count"] = len(rows)
        self.save()

    def verify(self, events):
        preview_ids = [preview["event_id"] for frame in self.document["frames"] for preview in frame["previews"]]
        if preview_ids != [event["event_id"] for event in events]:
            raise SimulationError("Dry-run previews differ from the verified event history")

    def set_status(self, status):
        self.document["status"] = status
        self.save()
