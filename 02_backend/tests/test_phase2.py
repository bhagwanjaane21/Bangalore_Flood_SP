import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings, load_settings
from app.main import create_app
from app.telemetry import utc_timestamp


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "telemetry.db")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def observation(depth=0.5, minutes=0, **changes):
    payload = {
        "node_id": "SENSOR_01", "water_depth_m": depth, "source": "simulated",
        "observed_at": utc_timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)),
    }
    return payload | changes


@pytest.mark.parametrize("changes", [
    {"water_depth_m": -1}, {"water_depth_m": True}, {"water_depth_m": "0.2"},
    {"water_depth_m": None}, {"water_depth_m": float("nan")},
    {"water_depth_m": float("inf")}, {"water_depth_m": -float("inf")},
    {"battery_voltage": -0.1}, {"battery_voltage": True},
    {"battery_voltage": float("nan")}, {"rate_of_rise_cm_min": float("inf")},
    {"rate_of_rise_cm_min": False}, {"source": "unrecognized"},
    {"node_id": ""}, {"node_id": 1}, {"observed_at": 1234},
    {"observed_at": "2026-01-01T12:00:00"}, {"observed_at": "not-a-date"},
])
def test_invalid_telemetry_is_rejected_without_writing(client, changes):
    response = client.post("/api/telemetry", content=json.dumps(observation(**changes)),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert client.get("/api/sensors/history/SENSOR_01").json() == []


def test_unknown_sensor_is_rejected(client):
    assert client.post("/api/telemetry", json=observation(node_id="MISSING")).status_code == 400


def test_old_payload_and_unknown_optional_values(client):
    reading = client.post("/api/telemetry", json={"node_id": "SENSOR_01", "water_depth_m": 0}).json()
    assert reading["status"] == "logged"
    assert reading["timestamp_source"] == "server_received"
    assert reading["source"] == "unknown"
    assert reading["observed_at"] == reading["received_at"]
    assert reading["rate_of_rise_cm_min"] is None
    assert reading["battery_voltage"] is None
    assert reading["rate_source"] == "unavailable"


def test_timezone_normalization_and_duplicate(client):
    first = client.post("/api/telemetry", json=observation(observed_at="2026-01-01T05:30:00+05:30")).json()
    second = client.post("/api/telemetry", json=observation()).json()
    assert first["observed_at"] == "2026-01-01T00:00:00.000000Z"
    assert second["status"] == "duplicate"
    assert first["reading_id"] == second["reading_id"]
    assert first["received_at"] == second["received_at"]


def test_history_limits_order_and_node_isolation(client):
    assert client.get("/api/sensors/history/SENSOR_01").json() == []
    for minute in range(4):
        assert client.post("/api/telemetry", json=observation(minutes=minute)).status_code == 200
    assert client.post("/api/telemetry", json=observation(node_id="SENSOR_02")).status_code == 200
    rows = client.get("/api/sensors/history/SENSOR_01?limit=2").json()
    assert len(rows) == 2
    assert [row["observed_at"] for row in rows] == [observation(minutes=value)["observed_at"] for value in (2, 3)]
    assert all(row["node_id"] == "SENSOR_01" for row in rows)
    assert len({row["reading_id"] for row in rows}) == 2
    assert client.get("/api/sensors/history/MISSING").status_code == 404
    for limit in ("0", "1001", "abc", "1.5"):
        assert client.get(f"/api/sensors/history/SENSOR_01?limit={limit}").status_code == 422


def test_conflicts_and_late_duplicate_do_not_change_latest(client):
    original = client.post("/api/telemetry", json=observation()).json()
    assert client.post("/api/telemetry", json=observation(depth=0.6)).status_code == 409
    latest = client.post("/api/telemetry", json=observation(depth=1.0, minutes=5)).json()
    assert client.post("/api/telemetry", json=observation(minutes=2)).status_code == 409
    duplicate = client.post("/api/telemetry", json=observation()).json()
    assert duplicate["status"] == "duplicate"
    assert duplicate["reading_id"] == original["reading_id"]
    assert client.get("/api/sensors/status").json()["SENSOR_01"]["reading_id"] == latest["reading_id"]
    assert len(client.get("/api/sensors/history/SENSOR_01").json()) == 2


def test_concurrent_duplicate_submissions_create_one_row(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/api/telemetry", json=observation()), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert [response.json()["status"] for response in responses].count("logged") == 1
    assert len(client.get("/api/sensors/history/SENSOR_01").json()) == 1


def test_rise_and_fall_rates_are_computed_from_observation_times(client):
    assert client.post("/api/telemetry", json=observation(depth=0.5)).status_code == 200
    rising = client.post("/api/telemetry", json=observation(depth=0.6, minutes=5, rate_of_rise_cm_min=999)).json()
    falling = client.post("/api/telemetry", json=observation(depth=0.5, minutes=10)).json()
    assert rising["rate_of_rise_cm_min"] == 2.0
    assert rising["reported_rate_of_rise_cm_min"] == 999
    assert rising["rate_source"] == "calculated"
    assert falling["rate_of_rise_cm_min"] == -2.0


def test_history_survives_restart(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/telemetry", json=observation()).json()
    with TestClient(create_app(settings)) as client:
        history = client.get("/api/sensors/history/SENSOR_01").json()
        assert history[0]["reading_id"] == response["reading_id"]
        assert history[0]["observed_at"] == response["observed_at"]


def test_real_future_times_rejected_simulated_clock_explicit(client):
    future = utc_timestamp(datetime.now(timezone.utc) + timedelta(days=365))
    assert client.post("/api/telemetry", json=observation(source="hardware", observed_at=future)).status_code == 422
    response = client.post("/api/telemetry", json=observation(observed_at=future))
    assert response.status_code == 200
    status = client.get("/api/sensors/status").json()["SENSOR_01"]
    assert status["data_status"] == "fresh"
    assert status["source"] == "simulated"
    assert status["lead_time_source"] == "unvalidated_assumption"


def test_historical_real_observation_is_stale(client):
    assert client.post("/api/telemetry", json=observation(source="hardware")).status_code == 200
    assert client.get("/api/sensors/status").json()["SENSOR_01"]["data_status"] == "stale"


def test_receipt_freshness_and_duplicate_does_not_refresh_it(client, settings):
    response = client.post("/api/telemetry", json=observation()).json()
    past = utc_timestamp(datetime.now(timezone.utc) - timedelta(minutes=10))
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("UPDATE telemetry SET received_at = ?", (past,))
        connection.commit()
    assert client.post("/api/telemetry", json=observation()).json()["status"] == "duplicate"
    status = client.get("/api/sensors/status").json()["SENSOR_01"]
    assert status["reading_id"] == response["reading_id"]
    assert status["data_status"] == "stale"


def test_phase1_database_migrates_without_fabricating_old_timestamps(settings):
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("""CREATE TABLE telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,
            water_depth_m REAL NOT NULL, rate_of_rise_cm_min REAL DEFAULT 0,
            battery_voltage REAL DEFAULT 3.7, timestamp TEXT NOT NULL)""")
        connection.execute("INSERT INTO telemetry VALUES (1, 'SENSOR_01', 0.4, 1, 3.7, '2026-01-01 12:00:00')")
        connection.commit()
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            history = client.get("/api/sensors/history/SENSOR_01").json()
            assert len(history) == 1
            assert history[0]["observed_at"] is None
            assert history[0]["timestamp"] == "2026-01-01 12:00:00"
            assert history[0]["timestamp_source"] == "legacy_unknown"
            assert history[0]["reported_rate_of_rise_cm_min"] == 1
            assert client.get("/api/sensors/status").json()["SENSOR_01"]["data_status"] == "unknown"
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/telemetry", json=observation()).status_code == 200
        assert len(client.get("/api/sensors/history/SENSOR_01").json()) == 2


def test_stale_configuration_validation():
    assert load_settings({"STREAM_STALE_AFTER_SECONDS": "120"}).stale_after_seconds == 120
    for value in ("0", "-1", "bad"):
        with pytest.raises(ValueError, match="STREAM_STALE_AFTER_SECONDS"):
            load_settings({"STREAM_STALE_AFTER_SECONDS": value})


def geojson(kind="LineString", coordinates=None):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {}, "geometry": {
            "type": kind, "coordinates": coordinates if coordinates is not None else [[77.61, 13.05], [77.62, 13.05]],
        },
    }]}


def test_bypass_api_and_compatibility_alias(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "PROJECT_DIR", tmp_path)
    spatial = tmp_path / "01_spatial_data"
    spatial.mkdir()
    bypass = spatial / "manyata_bypass_1.6km.geojson"
    assert client.get("/api/spatial/bypass").status_code == 404
    bypass.write_text("", encoding="utf-8")
    assert client.get("/api/spatial/bypass").status_code == 404
    bypass.write_text(json.dumps(geojson()), encoding="utf-8")
    response = client.get("/api/spatial/bypass")
    assert response.status_code == 200
    assert response.json() == client.get("/01_spatial_data/manyata_bypass_1.6km.geojson").json()
    assert 1000 < response.json()["metadata"]["geometry_length_m"] < 1200
    assert response.json()["metadata"]["verification_status"] == "unverified"


@pytest.mark.parametrize("data", [
    "not-json", "[]", '{}', '{"type":"FeatureCollection","features":null}',
    json.dumps(geojson(kind="Polygon")), json.dumps(geojson(coordinates=[[77, 91], [77, 13]])),
    json.dumps(geojson(coordinates=[[77, 13]])), json.dumps(geojson(coordinates=[[True, 13], [77, 13]])),
    json.dumps(geojson(coordinates=[[float("nan"), 13], [77, 13]])),
])
def test_bad_spatial_data_is_reported_as_unavailable(client, tmp_path, monkeypatch, data):
    monkeypatch.setattr(main_module, "PROJECT_DIR", tmp_path)
    spatial = tmp_path / "01_spatial_data"
    spatial.mkdir()
    (spatial / "manyata_bypass_1.6km.geojson").write_text(data, encoding="utf-8")
    assert client.get("/api/spatial/bypass").status_code == 503


def test_missing_sensor_file_and_point_validation(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "PROJECT_DIR", tmp_path)
    spatial = tmp_path / "01_spatial_data"
    spatial.mkdir()
    assert client.get("/api/spatial/sensors").status_code == 404
    sensor_file = spatial / "sensors.geojson"
    sensor_file.write_text(json.dumps(geojson(kind="Point", coordinates=[77.6, 13.0])), encoding="utf-8")
    assert client.get("/api/spatial/sensors").status_code == 200
    sensor_file.write_text(json.dumps(geojson(kind="Point", coordinates=[181, 13.0])), encoding="utf-8")
    assert client.get("/api/spatial/sensors").status_code == 503
