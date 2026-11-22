import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, DEFAULT_DATABASE_PATH, PROJECT_DIR, Settings, load_settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "nested db #demo" / "telemetry.db")


def test_paths_are_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_settings({}).database_path == DEFAULT_DATABASE_PATH
    assert load_settings({"STREAM_DB_PATH": "data/other.db"}).database_path == (
        BACKEND_DIR / "data" / "other.db"
    )
    assert Settings(database_path="data/direct.db").database_path == (
        BACKEND_DIR / "data" / "direct.db"
    )


@pytest.mark.parametrize("value", ["not-a-port", "0", "65536"])
def test_invalid_environment_port_is_clear(value):
    with pytest.raises(ValueError, match="STREAM_PORT"):
        load_settings({"STREAM_PORT": value})


def test_environment_overrides(tmp_path):
    settings = load_settings({
        "STREAM_DB_PATH": str(tmp_path / "custom.db"),
        "STREAM_HOST": "0.0.0.0",
        "STREAM_PORT": "8001",
        "STREAM_CORS_ORIGINS": " http://localhost:5173, ,http://127.0.0.1:5173 ",
    })
    assert settings.database_path == tmp_path / "custom.db"
    assert (settings.host, settings.port) == ("0.0.0.0", 8001)
    assert settings.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")


def test_fresh_database_and_existing_routes(settings):
    application = create_app(settings)
    assert not settings.database_path.exists()
    with TestClient(application) as client:
        health = client.get("/health").json()
        assert {key: health[key] for key in ("status", "database", "mode")} == {
            "status": "ok", "database": "ready", "mode": "prototype",
        }
        assert len(health["event_stream_id"]) == 36
        assert client.get("/docs").status_code == 200
        assert "/api/telemetry" in client.get("/openapi.json").json()["paths"]
        assert "STREAM" in client.get("/").text
        sensors = client.get("/api/spatial/sensors").json()
        assert sensors["type"] == "FeatureCollection"
        assert len(sensors["features"]) == 3
        assert client.get("/api/sensors/status").json() == {}
    # Explicitly closing SQLite connections matters on Windows.
    settings.database_path.unlink()


@pytest.mark.parametrize("depth,level", [
    (0.20, "GREEN"), (0.85, "YELLOW"), (1.25, "ORANGE"), (1.65, "RED"),
])
def test_existing_telemetry_contract(settings, depth, level):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/telemetry", json={
            "node_id": "SENSOR_01", "water_depth_m": depth,
            "rate_of_rise_cm_min": 2.0, "battery_voltage": 3.8,
        })
        assert response.status_code == 200
        expected = {
            "status": "logged", "node_id": "SENSOR_01", "alert_level": level,
            "lead_time": "35–45 mins", "recorded_depth_m": depth,
        }
        assert {key: response.json()[key] for key in expected} == expected
        status = client.get("/api/sensors/status").json()["SENSOR_01"]
        assert status["alert_level"] == level
        assert status["water_depth_m"] == depth
        assert status["rate_of_rise_cm_min"] == 2.0
        assert status["battery_voltage"] == 3.8
        assert status["last_updated"]
        assert client.post("/api/telemetry", json={
            "node_id": "UNKNOWN", "water_depth_m": depth,
        }).status_code == 400


def test_database_survives_restart(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/telemetry", json={
            "node_id": "SENSOR_02", "water_depth_m": 0.75,
        }).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/sensors/status").json()["SENSOR_02"]["water_depth_m"] == 0.75


def test_health_detects_removed_database_without_recreating_it(settings):
    with TestClient(create_app(settings)) as client:
        settings.database_path.unlink()
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        assert not settings.database_path.exists()


def test_startup_fails_for_invalid_database_target(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        with TestClient(create_app(Settings(database_path=tmp_path))):
            pytest.fail("A directory cannot be a SQLite database")


def test_startup_fails_for_incompatible_schema(settings):
    settings.database_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("CREATE TABLE telemetry (id INTEGER PRIMARY KEY)")
        connection.commit()
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        with TestClient(create_app(settings)):
            pytest.fail("An incompatible schema must not report readiness")


def test_configured_frontend_origin(settings):
    with TestClient(create_app(settings)) as client:
        response = client.options("/api/telemetry", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5500"
        assert "access-control-allow-credentials" not in response.headers
        denied = client.options("/api/telemetry", headers={
            "Origin": "http://unconfigured.example",
            "Access-Control-Request-Method": "POST",
        })
        assert denied.status_code == 400


@contextmanager
def running_backend(cwd, database_path, log_path, backend_dir=BACKEND_DIR):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("STREAM_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if database_path is not None:
        env["STREAM_DB_PATH"] = str(database_path)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(backend_dir / "run.py"), "--port", str(port)],
            cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding="utf-8"))
                    try:
                        if client.get("/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("Backend did not become ready: " + log_path.read_text(encoding="utf-8"))
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("launch_directory", ["root", "backend", "elsewhere"])
def test_real_launcher_from_multiple_directories(tmp_path, launch_directory):
    cwd = {"root": PROJECT_DIR, "backend": BACKEND_DIR, "elsewhere": tmp_path}[launch_directory]
    database_path = tmp_path / "fresh" / "telemetry.db"
    assert not database_path.exists()
    with running_backend(cwd, database_path, tmp_path / "server.log") as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/docs").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/api/spatial/sensors").status_code == 200
        assert client.get("/api/sensors/status").json() == {}
        assert client.post("/api/telemetry", json={
            "node_id": "SENSOR_03", "water_depth_m": 1.7,
        }).json()["alert_level"] == "RED"
    database_path.unlink()


def test_fresh_source_copy_uses_empty_default_database(tmp_path):
    checkout = tmp_path / "fresh checkout #demo"
    for directory in ("02_backend", "01_spatial_data", "03_dashboard"):
        shutil.copytree(
            PROJECT_DIR / directory, checkout / directory,
            ignore=shutil.ignore_patterns("*.db", "*.db-*", "__pycache__", ".pytest_cache"),
        )
    assert not list(checkout.rglob("*.db"))
    backend = checkout / "02_backend"
    with running_backend(tmp_path, None, tmp_path / "fresh.log", backend) as client:
        assert client.get("/api/sensors/status").json() == {}
        assert client.get("/").status_code == 200
        assert client.get("/api/spatial/sensors").status_code == 200
        assert (backend / "data" / "telemetry.db").is_file()


def test_launcher_rejects_invalid_port(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("STREAM_")}
    result = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "run.py"), "--port", "0"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "between 1 and 65535" in result.stderr
