import sqlite3
import math
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
    return Settings(database_path=tmp_path / "alerts.db")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def payload(depth, second=0, node="SENSOR_01", **extra):
    return {"node_id": node, "water_depth_m": depth, "source": "simulated",
            "observed_at": utc_timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)
                                         + timedelta(seconds=second)), **extra}


def post(client, depth, second=0, **extra):
    response = client.post("/api/telemetry", json=payload(depth, second, **extra))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("node,depth,expected", [
    ("SENSOR_01", .499, "GREEN"), ("SENSOR_01", .5, "YELLOW"),
    ("SENSOR_01", .999, "YELLOW"), ("SENSOR_01", 1., "ORANGE"),
    ("SENSOR_01", 1.499, "ORANGE"), ("SENSOR_01", 1.5, "RED"),
    ("SENSOR_02", .599, "GREEN"), ("SENSOR_02", .6, "YELLOW"),
    ("SENSOR_02", 1.099, "YELLOW"), ("SENSOR_02", 1.1, "ORANGE"),
    ("SENSOR_02", 1.599, "ORANGE"), ("SENSOR_02", 1.6, "RED"),
    ("SENSOR_03", .799, "GREEN"), ("SENSOR_03", .8, "YELLOW"),
    ("SENSOR_03", 1.199, "YELLOW"), ("SENSOR_03", 1.2, "ORANGE"),
    ("SENSOR_03", 1.499, "ORANGE"), ("SENSOR_03", 1.5, "RED"),
])
def test_boundaries_and_ingest_status_share_the_saved_decision(client, node, depth, expected):
    result = post(client, depth, node=node)
    status = client.get("/api/sensors/status").json()[node]
    assert result["alert_level"] == expected
    for field in ("alert_level", "alert_reason", "alert_kind", "alert_rule_version", "recovery_count"):
        assert status[field] == result[field]
    assert result["alert_reason"]
    assert result["lead_time_source"] == "unvalidated_assumption"


def test_local_red_is_immediate_without_other_sensors_or_rise_evidence(client):
    result = post(client, 1.5, node="SENSOR_03", rate_of_rise_cm_min=-999)
    assert result["alert_level"] == "RED"
    assert result["alert_kind"] == "depth"
    assert list(client.get("/api/sensors/status").json()) == ["SENSOR_03"]
    events = client.get("/api/alerts").json()
    assert len(events) == 1
    assert (events[0]["previous_level"], events[0]["new_level"]) == ("GREEN", "RED")
    assert events[0]["data_status_at_event"] == "fresh"


@pytest.mark.parametrize("increment,expected", [(.01, "YELLOW"), (.025, "ORANGE")])
def test_sustained_rise_warns_below_depth_thresholds(client, increment, expected):
    assert post(client, .2)["alert_level"] == "GREEN"
    assert post(client, .2 + increment, 30)["alert_level"] == "GREEN"
    result = post(client, .2 + 2 * increment, 60)
    assert result["alert_level"] == expected
    assert result["alert_kind"] == "trend"
    assert result["trend_sample_count"] == 3
    assert "Sustained rise" in result["alert_reason"]


def test_reported_rate_cannot_create_a_trend_warning(client):
    result = post(client, .2, rate_of_rise_cm_min=999)
    assert result["alert_level"] == "GREEN"
    assert "reported_rate_unverified" in result["quality_flags"]
    assert client.get("/api/alerts").json() == []


def test_large_finite_trends_remain_serializable(client):
    post(client, 0)
    post(client, 1e306, 60)
    result = post(client, 2e306, 120)
    assert result["alert_level"] == "RED"
    assert math.isfinite(result["trend_rate_cm_min"])


@pytest.mark.parametrize("depths,seconds", [
    ((.2, .201, .4), (0, 30, 60)),  # One isolated fast rise is insufficient.
    ((.2, .4, .2), (0, 30, 60)),  # An up/down spike is not sustained rise.
    ((.2, .21, .22), (0, 1, 2)),  # A burst of requests is not enough observation time.
    ((.2, .21, .22), (0, 30, 200)),  # A sampling gap breaks trend evidence.
])
def test_incomplete_or_inconsistent_trend_evidence_does_not_warn(client, depths, seconds):
    results = [post(client, depth, second) for depth, second in zip(depths, seconds)]
    assert results[-1]["alert_level"] == "GREEN"
    assert client.get("/api/alerts").json() == []


def test_source_change_resets_trend_evidence(client):
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    for index, source in enumerate(("simulated", "simulated", "hardware")):
        result = post(client, .2 + index * .025, source=source,
                      observed_at=utc_timestamp(base + timedelta(seconds=index * 30)))
    assert result["alert_level"] == "GREEN"
    assert "source_changed" in result["quality_flags"]


def test_recovery_deadband_distinct_samples_and_event_deduplication(client):
    post(client, 1.6)
    assert post(client, 1.49, 30)["recovery_count"] == 0
    assert post(client, 1.49, 60)["alert_level"] == "RED"
    first_low = post(client, 1.45, 90)
    assert first_low["alert_level"] == "RED"
    assert first_low["recovery_count"] == 1
    assert post(client, 1.45, 90)["status"] == "duplicate"
    assert client.get("/api/sensors/status").json()["SENSOR_01"]["recovery_count"] == 1
    recovered = post(client, 1.45, 120)
    assert recovered["alert_level"] == "ORANGE"
    assert recovered["alert_kind"] == "recovery"
    assert recovered["recovery_count"] == 0
    events = client.get("/api/alerts").json()
    assert [event["event_type"] for event in events] == ["escalation", "recovery"]
    assert [event["new_level"] for event in events] == ["RED", "ORANGE"]


def test_an_isolated_critical_spike_is_not_discarded(client):
    post(client, .2)
    assert post(client, 1.6, 30)["alert_level"] == "RED"
    assert post(client, .2, 60)["alert_level"] == "RED"
    assert post(client, .2, 90)["alert_level"] == "GREEN"


def test_recovery_evidence_resets_after_bounce_or_sampling_gap(client):
    post(client, 1.6)
    assert post(client, .2, 30)["recovery_count"] == 1
    assert post(client, 1.6, 60)["recovery_count"] == 0
    assert post(client, .2, 90)["recovery_count"] == 1
    assert post(client, .2, 300)["recovery_count"] == 1
    assert post(client, .2, 330)["alert_level"] == "GREEN"


def test_stale_observations_cannot_confirm_recovery(client, monkeypatch):
    base = datetime.now(timezone.utc)
    current = [base]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(main_module, "datetime", Clock)
    post(client, 1.6, source="hardware", observed_at=utc_timestamp(base))
    current[0] = base + timedelta(minutes=10)
    for second in (30, 60):
        result = post(client, .2, source="hardware", observed_at=utc_timestamp(base + timedelta(seconds=second)))
        assert result["data_status"] == "stale"
        assert result["alert_level"] == "RED"
        assert result["recovery_count"] == 0
    assert len(client.get("/api/alerts").json()) == 1


def test_old_buffered_samples_cannot_supply_a_new_fresh_trend_warning(client):
    base = datetime.now(timezone.utc)
    readings = [
        post(client, depth, source="hardware", observed_at=utc_timestamp(base + timedelta(seconds=offset)))
        for offset, depth in ((-132, .20), (-102, .23), (-72, .26), (-42, .29), (-12, .32))
    ]
    assert [r["data_status"] for r in readings] == ["stale", "stale", "fresh", "fresh", "fresh"]
    assert [r["alert_level"] for r in readings] == ["GREEN", "GREEN", "GREEN", "GREEN", "ORANGE"]
    assert "stale_trend_evidence" in readings[2]["quality_flags"]
    assert readings[2]["trend_sample_count"] == 1
    assert readings[-1]["trend_sample_count"] == 3
    events = client.get("/api/alerts").json()
    assert [event["reading_id"] for event in events] == [readings[-1]["reading_id"]]


def test_on_time_hardware_samples_remain_valid_through_a_longer_trend_window(client, monkeypatch):
    start = datetime.now(timezone.utc)

    class Clock(datetime):
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(main_module, "datetime", Clock)
    for index, depth in enumerate((.20, .25, .30)):
        Clock.current = start + timedelta(seconds=index * 60)
        reading = post(client, depth, source="hardware", observed_at=utc_timestamp(Clock.current))
    assert reading["trend_sample_count"] == 3
    assert (reading["alert_level"], reading["alert_kind"]) == ("ORANGE", "trend")


def test_status_reads_do_not_mutate_decisions_or_create_events(client, settings):
    result = post(client, 1.6)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("UPDATE telemetry SET received_at = ?", (
            utc_timestamp(datetime.now(timezone.utc) - timedelta(minutes=10)),))
        connection.commit()
    for _ in range(3):
        status = client.get("/api/sensors/status").json()["SENSOR_01"]
        assert status["data_status"] == "stale"
        assert status["alert_level"] == "RED"
        assert status["alert_reason"] == result["alert_reason"]
        assert status["data_warning"]
    assert len(client.get("/api/alerts").json()) == 1


def test_events_are_bounded_ordered_filterable_and_cursor_does_not_skip(client):
    assert client.get("/api/alerts").json() == []
    for depth, second in ((.85, 0), (1.25, 30), (1.65, 60)):
        post(client, depth, second)
    post(client, 1.7, node="SENSOR_02")
    events = client.get("/api/alerts").json()
    ids = [event["event_id"] for event in events]
    assert ids == sorted(ids) and len(ids) == 4
    assert [event["event_id"] for event in client.get("/api/alerts?limit=2").json()] == ids[-2:]
    assert [event["event_id"] for event in client.get(f"/api/alerts?after_id={ids[0]}&limit=2").json()] == ids[1:3]
    assert client.get(f"/api/alerts?after_id={2**64}").json() == []
    assert len(client.get("/api/alerts?node_id=SENSOR_02").json()) == 1
    assert client.get("/api/alerts?node_id=MISSING").status_code == 404
    for query in ("limit=0", "limit=1001", "limit=abc", "after_id=-1"):
        assert client.get("/api/alerts?" + query).status_code == 422


def test_recovery_and_events_survive_restart(settings):
    with TestClient(create_app(settings)) as client:
        post(client, 1.6)
        assert post(client, .2, 30)["recovery_count"] == 1
    with TestClient(create_app(settings)) as client:
        assert len(client.get("/api/alerts").json()) == 1
        assert post(client, .2, 60)["alert_level"] == "GREEN"
        assert len(client.get("/api/alerts").json()) == 2


def test_phase2_migration_bootstraps_state_without_replaying_historical_alerts(settings):
    old_receipt = utc_timestamp(datetime.now(timezone.utc) - timedelta(minutes=10))
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("""CREATE TABLE telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,
            water_depth_m REAL NOT NULL, rate_of_rise_cm_min REAL,
            battery_voltage REAL, timestamp TEXT NOT NULL, observed_at TEXT,
            received_at TEXT, source TEXT, timestamp_source TEXT,
            reported_rate_of_rise_cm_min REAL, rate_source TEXT)""")
        for second, depth in ((0, .2), (30, 1.6)):
            timestamp = payload(depth, second)["observed_at"]
            connection.execute("""INSERT INTO telemetry
                (node_id, water_depth_m, timestamp, observed_at, received_at, source)
                VALUES ('SENSOR_01', ?, ?, ?, ?, 'simulated')""",
                (depth, timestamp, timestamp, old_receipt))
        connection.commit()
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            status = client.get("/api/sensors/status").json()["SENSOR_01"]
            assert status["alert_level"] == "RED"
            assert status["data_status"] == "stale"
            assert client.get("/api/alerts").json() == []
    with TestClient(create_app(settings)) as client:
        assert post(client, .2, 60)["recovery_count"] == 1
        assert post(client, .2, 90)["alert_level"] == "GREEN"
        events = client.get("/api/alerts").json()
        assert len(events) == 1
        assert events[0]["event_type"] == "recovery"
        assert len(client.get("/api/sensors/history/SENSOR_01").json()) == 4


def test_concurrent_retries_do_not_advance_recovery_or_repeat_events(client):
    post(client, 1.6)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(client, .2, 30), range(4)))
    assert [result["status"] for result in results].count("logged") == 1
    assert client.get("/api/sensors/status").json()["SENSOR_01"]["recovery_count"] == 1
    assert len(client.get("/api/alerts").json()) == 1
    assert post(client, .2, 60)["alert_level"] == "GREEN"
    assert len(client.get("/api/alerts").json()) == 2


def test_failed_event_write_rolls_back_reading_and_state(client, settings):
    original = post(client, .2)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("""CREATE TRIGGER fail_event BEFORE INSERT ON alert_events
            BEGIN SELECT RAISE(ABORT, 'test event failure'); END""")
        connection.commit()
    failure = client.post("/api/telemetry", json=payload(1.6, 30))
    assert failure.status_code == 503
    assert "test event failure" not in failure.text
    assert client.get("/api/sensors/status").json()["SENSOR_01"]["reading_id"] == original["reading_id"]
    assert len(client.get("/api/sensors/history/SENSOR_01").json()) == 1
    assert client.get("/api/alerts").json() == []
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("DROP TRIGGER fail_event")
        connection.commit()
    assert post(client, 1.6, 30)["alert_level"] == "RED"


@pytest.mark.parametrize("environment,key", [
    ({"STREAM_RECOVERY_SAMPLES": "0"}, "STREAM_RECOVERY_SAMPLES"),
    ({"STREAM_RECOVERY_SAMPLES": "bad"}, "STREAM_RECOVERY_SAMPLES"),
    ({"STREAM_RECOVERY_MARGIN_M": "-1"}, "STREAM_RECOVERY_MARGIN_M"),
    ({"STREAM_RECOVERY_MARGIN_M": "nan"}, "STREAM_RECOVERY_MARGIN_M"),
    ({"STREAM_RECOVERY_MARGIN_M": ".3"}, "STREAM_RECOVERY_MARGIN_M"),
    ({"STREAM_RECOVERY_MARGIN_M": "bad"}, "STREAM_RECOVERY_MARGIN_M"),
    ({"STREAM_TREND_MIN_SPAN_SECONDS": "0"}, "STREAM_TREND_MIN_SPAN_SECONDS"),
    ({"STREAM_TREND_MIN_SPAN_SECONDS": "inf"}, "STREAM_TREND_MIN_SPAN_SECONDS"),
    ({"STREAM_TREND_MIN_SPAN_SECONDS": "bad"}, "STREAM_TREND_MIN_SPAN_SECONDS"),
])
def test_policy_configuration_validation(environment, key):
    with pytest.raises(ValueError, match=key):
        load_settings(environment)
