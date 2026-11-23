import importlib.util
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main_module
import simulation.cli as cli_module
from app.config import BACKEND_DIR, Settings, load_settings
from app.main import create_app
from simulation.cli import main
from simulation.cli import OwnedBackend
from simulation.runner import HttpApi, ScenarioRunner, SimulationCancelled, SimulationError
from simulation.scenarios import SCENARIOS


class VirtualClock:
    def __init__(self):
        self.seconds = 0.
        self.origin = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def monotonic(self):
        return self.seconds

    def wait(self, seconds):
        self.seconds += seconds


class LocalApi:
    def __init__(self, client):
        self.client = client

    def _get(self, path):
        response = self.client.get(path)
        assert response.status_code == 200, response.text
        return response.json()

    def health(self):
        return self._get("/health")

    def status(self):
        return self._get("/api/sensors/status")

    def alerts(self):
        return self._get("/api/alerts?limit=1000")

    def history(self, node):
        return self._get(f"/api/sensors/history/{node}?limit=1000")

    def ingest(self, payload):
        response = self.client.post("/api/telemetry", json=payload)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    clock = VirtualClock()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock.origin + timedelta(seconds=clock.seconds)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(main_module, "datetime", Clock)
    with TestClient(create_app(Settings(database_path=tmp_path / "demo.db"))) as client:
        yield LocalApi(client), clock


def run_local(local_api, name, **kwargs):
    api, clock = local_api
    return ScenarioRunner(SCENARIOS[name], api, run_id=str(uuid4()),
                          observed_start=clock.origin, monotonic=clock.monotonic,
                          wait=clock.wait, log=lambda line: None, **kwargs).run()


@pytest.mark.parametrize("name", tuple(SCENARIOS))
def test_every_scenario_matches_expected_levels_events_and_history(local_api, name):
    report = run_local(local_api, name, speed=30)
    assert report["status"] == "completed" and report["verified"] is True
    assert report["history_counts"] == report["expected_history_counts"]
    assert all(value["source"] == "simulated" for value in report["final_status"].values())
    assert len(set(report["reading_ids"])) == len(report["reading_ids"])
    if name == "sensor-dropout":
        assert report["elapsed_wall_seconds"] >= 90
        assert report["final_status"]["SENSOR_02"]["data_status"] == "stale"
        assert report["final_status"]["SENSOR_01"]["data_status"] == "fresh"
        assert report["final_status"]["SENSOR_03"]["data_status"] == "fresh"
    if name == "isolated-spike":
        assert report["events"] == []
    if name == "recovery":
        assert local_api[0].history("SENSOR_03")[2]["rate_of_rise_cm_min"] < 0


def test_playback_speed_changes_wall_time_not_observation_time_or_rates(tmp_path, monkeypatch):
    results = []
    for index, speed in enumerate((1., 1000.)):
        clock = VirtualClock()

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.origin + timedelta(seconds=clock.seconds)

        monkeypatch.setattr(main_module, "datetime", Clock)
        with TestClient(create_app(Settings(database_path=tmp_path / f"speed-{index}.db"))) as client:
            api = LocalApi(client)
            runner = ScenarioRunner(SCENARIOS["sustained-rise"], api, run_id=str(uuid4()),
                                    speed=speed, observed_start=clock.origin,
                                    monotonic=clock.monotonic, wait=clock.wait, log=lambda line: None)
            report = runner.run()
            results.append((report, api.history("SENSOR_01")))
    assert results[0][0]["elapsed_wall_seconds"] > results[1][0]["elapsed_wall_seconds"]
    for field in ("observed_at", "water_depth_m", "rate_of_rise_cm_min", "alert_level"):
        assert [row[field] for row in results[0][1]] == [row[field] for row in results[1][1]]


def test_existing_data_is_rejected_without_resetting_it(local_api):
    api, clock = local_api
    original = api.ingest({"node_id": "SENSOR_01", "water_depth_m": .2})
    with pytest.raises(SimulationError, match="existing data"):
        run_local(local_api, "normal")
    assert api.status()["SENSOR_01"]["reading_id"] == original["reading_id"]
    assert len(api.history("SENSOR_01")) == 1


def test_cancellation_preserves_partial_report_and_stops_new_posts(local_api):
    api, clock = local_api
    cancelled = threading.Event()
    original_ingest = api.ingest

    def ingest_then_cancel(payload):
        reading = original_ingest(payload)
        cancelled.set()
        return reading

    api.ingest = ingest_then_cancel
    runner = ScenarioRunner(SCENARIOS["normal"], api, run_id=str(uuid4()),
                            cancel_event=cancelled, log=lambda line: None)
    with pytest.raises(SimulationCancelled):
        runner.run()
    assert runner.report["status"] == "cancelled"
    assert runner.report["verified"] is False
    assert len(runner.report["reading_ids"]) == 1
    assert len(api.history("SENSOR_01")) == 1
    assert api.history("SENSOR_02") == []


def test_failed_checkpoint_never_reports_success(local_api):
    api, clock = local_api
    real_status = api.status

    def wrong_status():
        result = real_status()
        for reading in result.values():
            reading["alert_level"] = "RED"
        return result

    api.status = wrong_status
    runner = ScenarioRunner(SCENARIOS["normal"], api, run_id=str(uuid4()), log=lambda line: None)
    with pytest.raises(SimulationError, match="expected levels"):
        runner.run()
    assert runner.report["status"] == "failed"
    assert runner.report["verified"] is False


def test_persisted_payload_must_match_the_scenario(local_api):
    api, clock = local_api
    original_history = api.history

    def corrupted_history(node):
        rows = original_history(node)
        if rows:
            rows[0]["water_depth_m"] = 99
        return rows

    api.history = corrupted_history
    runner = ScenarioRunner(SCENARIOS["normal"], api, run_id=str(uuid4()), speed=1000,
                            observed_start=clock.origin, monotonic=clock.monotonic,
                            wait=clock.wait, log=lambda line: None)
    with pytest.raises(SimulationError, match="Persisted reading differs"):
        runner.run()
    assert runner.report["verified"] is False


def test_same_level_with_the_wrong_snapshot_cannot_be_recorded_as_a_checked_step(local_api):
    api, clock = local_api
    original_status = api.status
    captured = []

    def wrong_depth():
        snapshot = original_status()
        if snapshot:
            snapshot["SENSOR_02"]["water_depth_m"] = .01
        return snapshot

    api.status = wrong_depth
    runner = ScenarioRunner(SCENARIOS["normal"], api, run_id=str(uuid4()), speed=1000,
                            observed_start=clock.origin, monotonic=clock.monotonic,
                            wait=clock.wait, log=lambda line: None,
                            on_checkpoint=lambda *args: captured.append(args))
    with pytest.raises(SimulationError, match="SENSOR_02.*latest observation"):
        runner.run()
    assert runner.report["verified"] is False
    assert captured == []


def test_lost_post_response_retries_same_observation_without_duplicate_rows(local_api):
    api, clock = local_api
    http = HttpApi("http://testserver")
    http.session.close()

    class LostAckSession:
        lost = False

        def request(self, method, url, json=None, timeout=None):
            response = api.client.request(method, url, json=json)
            if method == "POST" and not self.lost:
                self.lost = True
                raise requests.Timeout("test lost acknowledgment")
            return response

        def close(self):
            pass

    http.session = LostAckSession()
    result = http.ingest({"node_id": "SENSOR_01", "water_depth_m": .2,
                          "observed_at": "2026-01-01T00:00:00.000000Z", "source": "simulated"})
    assert result["status"] == "duplicate"
    assert len(api.history("SENSOR_01")) == 1
    http.close()


def test_cli_list_and_dry_run_do_not_create_reports_or_contact_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module.HttpApi, "__init__", lambda *args, **kwargs: pytest.fail("No network expected"))
    assert main(["--list"]) == 0
    output = capsys.readouterr().out
    assert all(name in output for name in SCENARIOS)
    output_dir = tmp_path / "not-created"
    assert main(["normal", "--dry-run", "--report-dir", str(output_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "normal"
    assert not output_dir.exists()


@pytest.mark.parametrize("args", [
    ["normal", "--speed", "0"], ["normal", "--speed", "nan"],
    ["normal", "--speed", "inf"], ["normal", "--speed", ".1"],
    ["normal", "--port", "65536"], ["normal", "--stale-after-seconds", "0"],
    ["normal", "--stale-after-seconds", "301"], ["normal", "--stale-after-seconds", "2"],
    ["normal", "--observed-start", "2026-01-01T00:00:00"],
    ["normal", "--observed-start", "0001-01-01T00:00:00+02:00"],
    ["normal", "--base-url", "file:///tmp/demo"],
    ["normal", "--base-url", "http://localhost:8000", "--keep-server"],
])
def test_invalid_cli_configuration_is_rejected(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_simulation_run_identity_is_optional_and_validated(tmp_path):
    run_id = str(uuid4())
    settings = load_settings({"STREAM_DB_PATH": str(tmp_path / "identity.db"), "STREAM_SIMULATION_RUN_ID": run_id})
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["simulation_run_id"] == run_id
    with pytest.raises(ValueError, match="STREAM_SIMULATION_RUN_ID"):
        load_settings({"STREAM_SIMULATION_RUN_ID": "not-a-uuid"})


def test_legacy_entry_points_are_import_safe_and_finite(monkeypatch):
    monkeypatch.setattr(cli_module, "main", lambda *args, **kwargs: pytest.fail("Import must not start playback"))
    for filename in ("continuous_sim.py", "mock_nodes.py"):
        spec = importlib.util.spec_from_file_location("test_legacy_sim", BACKEND_DIR / filename)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize("scenario,extra", [("progressing-surge", []), ("sensor-dropout", ["--stale-after-seconds", "1"])])
def test_real_cli_owns_server_verifies_run_and_closes_it(tmp_path, scenario, extra):
    result = subprocess.run(
        [sys.executable, "-B", str(BACKEND_DIR / "simulate.py"), scenario, "--port", "0",
         "--speed", "1000000", "--report-dir", str(tmp_path), *extra],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list(tmp_path.glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "completed" and report["verified"] is True
    assert report["backend_owned"] is True
    assert (reports[0].parent / "telemetry.db").is_file()
    assert "COMPLETED" in result.stdout
    with requests.Session() as session:
        session.trust_env = False
        with pytest.raises(requests.ConnectionError):
            session.get(report["base_url"] + "/health", timeout=1)


def test_new_run_does_not_post_to_an_occupied_server_port(tmp_path):
    original_folder = tmp_path / "original"
    original_folder.mkdir()
    original = OwnedBackend(original_folder, str(uuid4()), 0, 90)
    try:
        api = original.start()
        assert main(["normal", "--port", str(original.port), "--speed", "1000000",
                     "--report-dir", str(tmp_path / "attempt")]) == 1
        assert original.process.poll() is None
        assert api.status() == {}
        assert api.alerts() == []
        report_file = next((tmp_path / "attempt").glob("*/report.json"))
        assert json.loads(report_file.read_text(encoding="utf-8"))["verified"] is False
    finally:
        original.close()


def test_replays_use_fresh_databases_and_ignore_inherited_backend_overrides(tmp_path, monkeypatch):
    sentinel = tmp_path / "existing-user-data.db"
    sentinel.write_text("existing data must remain intact", encoding="utf-8")
    monkeypatch.setenv("STREAM_DB_PATH", str(sentinel))
    monkeypatch.setenv("STREAM_PORT", "invalid")
    monkeypatch.setenv("STREAM_RECOVERY_SAMPLES", "999")
    monkeypatch.setenv("STREAM_SIMULATION_RUN_ID", "invalid")
    report_parent = tmp_path / "runs"
    for _ in range(2):
        assert main(["recovery", "--port", "0", "--speed", "1000000",
                     "--observed-start", "2026-01-01T00:00:00Z",
                     "--report-dir", str(report_parent)]) == 0
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_parent.glob("*/report.json")]
    assert len(reports) == 2
    assert reports[0]["run_id"] != reports[1]["run_id"]
    assert reports[0]["history_counts"] == reports[1]["history_counts"]
    assert reports[0]["reading_ids"] == reports[1]["reading_ids"]
    assert all(report["verified"] for report in reports)
    assert len(list(report_parent.glob("*/telemetry.db"))) == 2
    assert sentinel.read_text(encoding="utf-8") == "existing data must remain intact"


def test_explicit_external_server_is_not_stopped_or_reset(tmp_path):
    server_folder = tmp_path / "server"
    server_folder.mkdir()
    server = OwnedBackend(server_folder, str(uuid4()), 0, 90)
    try:
        api = server.start()
        args = ["normal", "--base-url", api.base_url, "--speed", "1000000",
                "--report-dir", str(tmp_path / "attached")]
        assert main(args) == 0
        reports = list((tmp_path / "attached").glob("*/report.json"))
        assert json.loads(reports[0].read_text(encoding="utf-8"))["backend_owned"] is False
        previous = api.history("SENSOR_01")
        assert main(args) == 1  # Existing observations must not be silently reset.
        assert api.history("SENSOR_01") == previous
        assert server.process.poll() is None
    finally:
        server.close()


@pytest.mark.parametrize("keep_server,preview_exit,exit_code", [
    (False, None, 130), (True, None, 0), (True, 1, 1),
])
def test_cli_cancellation_and_keep_server_cleanup(tmp_path, monkeypatch, keep_server, preview_exit, exit_code):
    closed = []

    class FakeOwned:
        process = None

        def __init__(self, *args):
            self.process = self

        def start(self):
            return type("Api", (), {"base_url": "http://testserver"})()

        def poll(self):
            return preview_exit

        def close(self):
            closed.append(True)

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            self.report = {"status": "running", "verified": False}

        def run(self):
            if not keep_server:
                raise KeyboardInterrupt()
            self.report.update(status="completed", verified=True, reading_ids=[], events=[])
            return self.report

    monkeypatch.setattr(cli_module, "OwnedBackend", FakeOwned)
    monkeypatch.setattr(cli_module, "ScenarioRunner", FakeRunner)
    monkeypatch.setattr(cli_module.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    args = ["normal", "--report-dir", str(tmp_path)] + (["--keep-server"] if keep_server else [])
    assert main(args) == exit_code
    report_file = next(tmp_path.glob("*/report.json"))
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["status"] == ("completed" if keep_server else "cancelled")
    assert report["verified"] is keep_server
    if preview_exit is not None:
        assert report["preview_exit_code"] == preview_exit
    assert closed == [True]
