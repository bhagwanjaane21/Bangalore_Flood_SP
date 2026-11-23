import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from simulation.cli import main
from simulation.runner import ScenarioRunner
from simulation.scenarios import SCENARIOS


def test_monitoring_server_serves_only_dashboard_assets_and_never_creates_replay(tmp_path):
    settings = Settings(database_path=tmp_path / "monitor.db")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/review").json() == {"mode": "monitoring", "frames": []}
        for page in ("/", "/review"):
            html = client.get(page)
            assert html.status_code == 200
            assets = re.findall(r'(?:href|src)="(/assets/[^"]+)"', html.text)
            assert assets
            for asset in assets:
                response = client.get(asset)
                assert response.status_code == 200
                mime = response.headers["content-type"]
                assert any(kind in mime for kind in ("javascript", "text/css", "image/svg+xml"))
        assert client.get("/assets/README.md").status_code == 404
        assert not (tmp_path / "review.db").exists()


@pytest.mark.parametrize("profile", SCENARIOS)
def test_recorded_run_reopens_with_its_own_evidence(tmp_path, profile, monkeypatch):
    monkeypatch.setenv("STREAM_TELEGRAM_BOT_TOKEN", "test-ignored")
    args = [profile, "--port", "0", "--speed", "500", "--report-dir", str(tmp_path)]
    if profile == "sensor-dropout":
        args += ["--stale-after-seconds", "4"]
    assert main(args) == 0
    folder = next(tmp_path.iterdir())
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    settings = Settings(database_path=folder / "telemetry.db", simulation_run_id=folder.name)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/review")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        replay = response.json()
        assert replay["status"] == "completed" and replay["scenario"] == profile
        assert replay["total_frames"] == len(replay["frames"]) == len(report["checkpoints"])
        previews = [p for frame in replay["frames"] for p in frame["previews"]]
        assert len(previews) == replay["preview_count"] == report["dry_run_previews"] == len(SCENARIOS[profile].transitions)
        assert [p["event_id"] for p in previews] == [e["event_id"] for e in client.get("/api/alerts").json()]
        assert all("DRY RUN - NO MESSAGE SENT" in p["text"] for p in previews)
        for frame, checkpoint in zip(replay["frames"], report["checkpoints"], strict=True):
            for station in frame["stations"]:
                assert station["source"] == "simulated"
                assert station["data_status"] == checkpoint["data_status"][station["node_id"]]
                assert station["alert_level"] == checkpoint["actual_levels"][station["node_id"]]
        if profile == "recovery":
            assert replay["frames"][2]["stations"][2]["alert_kind"] == "held"
            assert replay["frames"][2]["previews"] == []
    unrelated = Settings(database_path=settings.database_path, simulation_run_id=str(uuid4()))
    with TestClient(create_app(unrelated)) as client:
        assert client.get("/api/review").status_code == 409


def test_failed_playback_retains_partial_evidence(tmp_path, monkeypatch):
    original = ScenarioRunner._checkpoint

    def fail_after_checkpoint(self, *args):
        original(self, *args)
        raise ValueError("interrupted test")

    monkeypatch.setattr(ScenarioRunner, "_checkpoint", fail_after_checkpoint)
    assert main(["normal", "--port", "0", "--report-dir", str(tmp_path)]) == 1
    folder = next(tmp_path.iterdir())
    with TestClient(create_app(Settings(database_path=folder / "telemetry.db", simulation_run_id=folder.name))) as client:
        replay = client.get("/api/review").json()
        assert replay["status"] == "failed"
        assert len(replay["frames"]) == 1 < replay["total_frames"]


def test_missing_and_unreadable_replays_do_not_affect_monitoring(tmp_path):
    settings = Settings(database_path=tmp_path / "telemetry.db", simulation_run_id=str(uuid4()))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/review").json()["status"] == "waiting"
        (tmp_path / "report.json").write_text("{}")
        assert client.get("/api/review").json()["status"] == "not_recorded"
        (tmp_path / "review.db").write_bytes(b"bad sqlite file")
        assert client.get("/api/review").status_code == 503
        assert client.get("/health").status_code == 200
