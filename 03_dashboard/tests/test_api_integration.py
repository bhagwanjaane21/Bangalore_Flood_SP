"""Verify the real API contract consumed by the integrated frontend."""

import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    database_path = tmp_path / "integration.db"
    env = {key: value for key, value in os.environ.items() if not key.startswith("STREAM_")}
    env.update(STREAM_DB_PATH=str(database_path), PYTHONDONTWRITEBYTECODE="1")
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(PROJECT_DIR / "02_backend" / "run.py"), "--port", str(port)],
            cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT,
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
                yield client, database_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_actual_sensor_and_bypass_files_are_served(server):
    client, _ = server
    assert client.get("/").status_code == 200
    sensors = client.get("/api/spatial/sensors").json()
    assert {feature["properties"]["Sensor_ID"] for feature in sensors["features"]} == {
        "SENSOR_01", "SENSOR_02", "SENSOR_03",
    }
    bypass = client.get("/api/spatial/bypass")
    assert bypass.status_code == 200
    assert bypass.json() == client.get("/01_spatial_data/manyata_bypass_1.6km.geojson").json()
    assert 990 < bypass.json()["metadata"]["geometry_length_m"] < 995
    assert bypass.json()["metadata"]["verification_status"] == "unverified"
    assert bypass.json()["features"][0]["properties"]["reported_length_km"] == 1.6


def test_live_sensor_history_duplicates_and_staleness(server):
    client, database_path = server
    assert client.get("/api/sensors/status").json() == {}
    for node in ("SENSOR_01", "SENSOR_02", "SENSOR_03"):
        payload = {"node_id": node, "water_depth_m": 0.2,
                   "observed_at": "2026-01-01T00:00:00Z", "source": "simulated"}
        assert client.post("/api/telemetry", json=payload).status_code == 200
    critical = {"node_id": "SENSOR_03", "water_depth_m": 1.65,
                "observed_at": "2026-01-01T00:01:00Z", "source": "simulated"}
    result = client.post("/api/telemetry", json=critical).json()
    assert result["alert_level"] == "RED"
    assert client.post("/api/telemetry", json=critical).json()["status"] == "duplicate"
    history = client.get("/api/sensors/history/SENSOR_03").json()
    assert len(history) == 2
    assert history[-1]["reading_id"] == result["reading_id"]
    assert history[-1]["source"] == "simulated"
    assert client.get("/api/sensors/history/SENSOR_03").json() == history
    assert all(reading["data_status"] == "fresh" for reading in client.get("/api/sensors/status").json().values())
    assert client.post("/api/telemetry", json={
        "node_id": "SENSOR_01", "water_depth_m": -1,
    }).status_code == 422
    old_receipt = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("UPDATE telemetry SET received_at = ? WHERE node_id = 'SENSOR_03'", (old_receipt,))
        connection.commit()
    assert client.get("/api/sensors/status").json()["SENSOR_03"]["data_status"] == "stale"
