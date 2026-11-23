"""No test contacts Telegram: sender responses and network failures are simulated."""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, PROJECT_DIR, Settings
from app.main import create_app
from app.telemetry import utc_timestamp
from notifications.cli import main
from notifications.outbox import Outbox
from notifications.transport import Outcome, TelegramSender
from notifications.worker import BackendClient, BackendUnavailable, Worker, message_card
from test_startup import running_backend


@pytest.fixture(autouse=True)
def forbid_live_telegram(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected external POST: tests must never contact Telegram")
    monkeypatch.setattr(requests.Session, "post", forbidden)


class APIBackend:
    def __init__(self, client):
        self.client = client

    def stream_id(self):
        return self.client.get("/health").json()["event_stream_id"]

    def get(self, path, **params):
        result = self.client.get(path, params=params)
        assert result.status_code == 200
        return result.json()


@pytest.fixture
def setup(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path / "telemetry.db"))) as client:
        outbox = Outbox(tmp_path / "notifications #state" / "delivery.db")
        backend = APIBackend(client)
        output = []
        yield SimpleNamespace(client=client, backend=backend, outbox=outbox, output=output,
                              worker=Worker(backend, outbox, emit=output.append))


def post(client, depth=0.8, *, node="SENSOR_01", offset=0, source="simulated"):
    response = client.post("/api/telemetry", json={
        "node_id": node, "water_depth_m": depth, "source": source,
        "observed_at": utc_timestamp(datetime.now(timezone.utc) + timedelta(seconds=offset)),
    })
    assert response.status_code == 200, response.text
    return response.json()


def live_worker(setup, outcomes=None, **options):
    sender = Mock()
    sender.send.side_effect = outcomes or [Outcome("delivered", "provider_accepted", message_id=123)]
    worker = Worker(setup.backend, setup.outbox, sender=sender, chat_id="-12345",
                    allow_simulated=True, emit=setup.output.append, **options)
    return worker, sender


def test_dry_run_is_durable_and_heartbeat_does_not_repeat(setup):
    post(setup.client)
    report = setup.worker.cycle()
    assert report["counts"] == {"dry_run": 1}
    assert report["cursor"] == 1
    assert "DRY RUN - NO MESSAGE SENT" in setup.output[0]
    assert "Source: simulated" in setup.output[0]
    assert "forecast" in setup.output[0]
    setup.worker = Worker(setup.backend, Outbox(setup.outbox.path), emit=setup.output.append)
    post(setup.client, offset=30)
    assert setup.worker.cycle()["counts"] == {"dry_run": 1}
    assert len(setup.output) == 1
    assert setup.outbox.rows()[0]["message_id"] is None


def test_preview_is_separate_from_live_and_success_is_not_repeated(setup):
    post(setup.client)
    setup.worker.cycle()
    worker, sender = live_worker(setup)
    assert worker.cycle()["counts"] == {"delivered": 1}
    assert worker.cycle()["counts"] == {"delivered": 1}
    assert sender.send.call_count == 1
    assert sorted(row["status"] for row in setup.outbox.rows()) == ["delivered", "dry_run"]
    assert setup.outbox.rows(worker.identity()[1])[0]["message_id"] == 123


def test_same_level_heartbeat_keeps_pending_transition_eligible(setup):
    post(setup.client)
    post(setup.client, offset=30)
    current = setup.backend.get("/api/sensors/status")["SENSOR_01"]
    assert current["reading_id"] == 2 and current["alert_event_id"] == 1
    assert setup.worker.cycle()["counts"] == {"dry_run": 1}


def test_current_transition_id_prevents_replaying_old_same_level_episode(setup):
    post(setup.client)
    post(setup.client, 0.1, offset=30)
    post(setup.client, 0.1, offset=60)
    post(setup.client, 0.8, offset=90)
    report = setup.worker.cycle()
    assert report["counts"] == {"skipped": 2, "dry_run": 1}
    assert setup.outbox.rows()[0]["detail"] == "superseded_transition"
    assert "GREEN -> YELLOW" in setup.output[-1]


def test_paginated_ingestion_queues_every_transition_without_skips(setup):
    for node in ("SENSOR_01", "SENSOR_02", "SENSOR_03"):
        post(setup.client, 1.7, node=node)
    worker = Worker(setup.backend, setup.outbox, batch_size=1, emit=setup.output.append)
    for expected in (1, 2, 3):
        assert worker.cycle()["cursor"] == expected
    assert worker.cycle()["more_events"] is False
    assert [row["event_id"] for row in setup.outbox.rows()] == [1, 2, 3]
    assert all(row["status"] == "dry_run" for row in setup.outbox.rows())


def test_failed_queue_transaction_does_not_advance_cursor(tmp_path):
    outbox = Outbox(tmp_path / "queue.db")
    with pytest.raises(ValueError):
        outbox.enqueue("demo", [{"event_id": 1}, {"event_id": 2, "bad": float("nan")}])
    assert outbox.cursor("demo") == 0
    assert outbox.rows() == []
    outbox.enqueue("demo", [{"event_id": 1}])
    outbox.enqueue("demo", [{"event_id": 1}])
    assert len(outbox.rows()) == 1


def test_backend_unavailable_does_not_consume_attempt_or_lose_queued_event(setup, monkeypatch):
    post(setup.client)
    original = setup.backend.get
    def unavailable(path, **params):
        if path == "/api/sensors/status":
            raise BackendUnavailable("offline")
        return original(path, **params)
    monkeypatch.setattr(setup.backend, "get", unavailable)
    with pytest.raises(BackendUnavailable):
        setup.worker.cycle()
    row = setup.outbox.rows()[0]
    assert row["status"] == "retry" and row["attempts"] == 0
    assert setup.outbox.cursor(setup.worker.identity()[1]) == 1
    monkeypatch.setattr(setup.backend, "get", original)
    setup.worker.clock = lambda: time.time() + 4
    assert setup.worker.cycle()["counts"] == {"dry_run": 1}


def test_failed_poll_does_not_advance_ingestion_cursor(setup, monkeypatch):
    post(setup.client)
    monkeypatch.setattr(setup.backend, "get", Mock(side_effect=BackendUnavailable("offline")))
    with pytest.raises(BackendUnavailable):
        setup.worker.cycle()
    assert setup.outbox.rows() == []
    assert setup.outbox.cursor(setup.worker.identity()[1]) == 0


def test_stream_identity_survives_restart_and_new_database_has_distinct_namespace(tmp_path):
    outbox = Outbox(tmp_path / "delivery.db")
    identities = []
    for filename in ("first.db", "first.db", "second.db"):
        with TestClient(create_app(Settings(database_path=tmp_path / filename))) as client:
            backend = APIBackend(client)
            identities.append(backend.stream_id())
            if not client.get("/api/sensors/status").json():
                post(client)
            Worker(backend, outbox, emit=lambda _: None).cycle()
    assert identities[0] == identities[1] != identities[2]
    assert all(str(uuid.UUID(value)) == value for value in identities)
    assert len(outbox.rows()) == 2
    assert [row["event_id"] for row in outbox.rows()] == [1, 1]


def test_stream_changed_during_poll_does_not_commit_wrong_events(setup, monkeypatch):
    post(setup.client)
    first = setup.backend.stream_id()
    monkeypatch.setattr(setup.backend, "stream_id", Mock(side_effect=[first, str(uuid.uuid4())]))
    with pytest.raises(BackendUnavailable, match="stream_changed"):
        setup.worker.cycle()
    assert setup.outbox.rows() == []


def test_atomic_claim_and_crash_recovery(tmp_path):
    one = Outbox(tmp_path / "delivery.db")
    two = Outbox(one.path)
    one.enqueue("live", [{"event_id": 1}])
    claimed = one.claim("live", 100, live=True, max_attempts=5)
    one.start_attempt("live", claimed)
    assert two.claim("live", 100, live=True, max_attempts=5) is None
    assert two.claim("live", 221, live=True, max_attempts=5) is None
    assert two.rows()[0]["status"] == "uncertain"
    two.retry_uncertain("live", 1)
    assert two.claim("live", 222, live=True, max_attempts=5)["attempts"] == 1
    with pytest.raises(RuntimeError, match="claim lost"):
        one.finish("live", claimed, "delivered", "late_ack")
    one.enqueue("dry", [{"event_id": 1}])
    assert one.claim("dry", 100, live=False, max_attempts=5)
    assert two.claim("dry", 221, live=False, max_attempts=5)


def test_missing_ledger_fails_without_silently_creating_new_state(tmp_path):
    outbox = Outbox(tmp_path / "delivery.db")
    outbox.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        outbox.rows()
    assert not outbox.path.exists()


def test_two_simultaneous_workers_cannot_claim_one_event(tmp_path):
    outbox = Outbox(tmp_path / "delivery.db")
    outbox.enqueue("live", [{"event_id": 1}])
    def claim(_):
        return Outbox(outbox.path).claim("live", 100, live=True, max_attempts=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(value is not None for value in claims) == 1


def test_phase4_database_migration_preserves_history_and_corrupt_identity_fails_readiness(tmp_path):
    settings = Settings(database_path=tmp_path / "old.db")
    with TestClient(create_app(settings)) as client:
        post(client)
        original_events = client.get("/api/alerts").json()
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TABLE backend_metadata")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/alerts").json() == original_events
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE backend_metadata SET value='broken'")
        assert client.get("/health").status_code == 503


def test_missing_identity_on_restart_preserves_original_notification_namespace(tmp_path):
    settings = Settings(database_path=tmp_path / "store.db")
    with TestClient(create_app(settings)) as client:
        post(client)
        saved_id = client.get("/health").json()["event_stream_id"]
        with sqlite3.connect(settings.database_path) as db:
            db.execute("DELETE FROM backend_metadata WHERE key = 'event_stream_id'")
        assert client.get("/health").status_code == 503
    with pytest.raises(sqlite3.DatabaseError, match="stream identity"):
        with TestClient(create_app(settings)):
            pytest.fail("Starting with a new identity would replay previously dispatched events")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM backend_metadata").fetchone()[0] == 0
        db.execute("INSERT INTO backend_metadata VALUES ('event_stream_id', ?)", (saved_id,))
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["event_stream_id"] == saved_id
        assert len(client.get("/api/alerts").json()) == 1


def test_card_is_bounded_plain_text_and_preserves_reference(setup):
    post(setup.client)
    event = setup.backend.get("/api/alerts")[0]
    event["reason"] = "<b>untrusted</b>" * 1000
    current = setup.backend.get("/api/sensors/status")["SENSOR_01"]
    current["name"] = "A" * 5000
    current["alert_action"] = "B" * 5000
    stream_id = setup.backend.stream_id()
    card = message_card(event, current, stream_id, dry_run=True)
    assert len(card) < 4096
    assert f"Reference: {stream_id}/1" in card


def test_429_backoff_persists_across_restart_and_blocks_destination(setup):
    now = time.time()
    post(setup.client)
    worker, sender = live_worker(setup, [Outcome("retry", "rate_limited", retry_after=30)], clock=lambda: now)
    assert worker.cycle()["counts"] == {"retry": 1}
    row = setup.outbox.rows(worker.identity()[1])[0]
    assert row["due_at"] == now + 30 and row["attempts"] == 1
    post(setup.client, 1.8, node="SENSOR_02")
    worker2, second = live_worker(setup, clock=lambda: now + 29)
    assert worker2.cycle()["counts"] == {"retry": 1, "pending": 1}
    second.send.assert_not_called()
    second.send.side_effect = [Outcome("delivered", "provider_accepted", message_id=11),
                               Outcome("delivered", "provider_accepted", message_id=12)]
    worker2.clock = lambda: now + 31
    assert worker2.cycle()["counts"] == {"delivered": 2}
    assert sender.send.call_count == 1


def test_retry_does_not_block_new_critical_alert_and_exhaustion_is_visible(setup):
    now = time.time()
    post(setup.client)
    worker, sender = live_worker(setup, [Outcome("retry", "provider_unavailable"),
        Outcome("delivered", "provider_accepted", message_id=9), Outcome("retry", "provider_unavailable")],
        max_attempts=2, clock=lambda: now)
    assert worker.cycle()["counts"] == {"retry": 1}
    post(setup.client, 1.8, node="SENSOR_02")
    assert worker.cycle()["counts"] == {"retry": 1, "delivered": 1}
    worker.clock = lambda: now + 3
    assert worker.cycle()["counts"] == {"failed": 1, "delivered": 1}
    worker.cycle()
    assert sender.send.call_count == 3


def test_lost_ack_needs_explicit_scoped_retry(setup):
    post(setup.client)
    worker, sender = live_worker(setup, [Outcome("uncertain", "transport_ack_unknown"),
                                        Outcome("delivered", "provider_accepted", message_id=5)])
    assert worker.cycle()["counts"] == {"uncertain": 1}
    assert worker.cycle()["counts"] == {"uncertain": 1}
    assert sender.send.call_count == 1
    with pytest.raises(ValueError):
        setup.outbox.retry_uncertain("different-destination", 1)
    setup.outbox.retry_uncertain(worker.identity()[1], 1)
    assert worker.cycle()["counts"] == {"delivered": 1}


@pytest.mark.parametrize("change,reason", [
    ({"new_level": "ORANGE"}, "superseded"),
    ({"source": "hardware"}, "superseded"),
    ({"data_status_at_event": "stale"}, "event_data_not_fresh"),
    ({"created_at": "2020-01-01T00:00:00Z"}, "event_expired"),
    ({"created_at": "2099-01-01T00:00:00Z"}, "event_clock_in_future"),
])
def test_ineligible_event_is_skipped_without_sending(setup, change, reason):
    post(setup.client)
    event = {**setup.backend.get("/api/alerts")[0], **change}
    scope = setup.worker.identity()[1]
    setup.outbox.enqueue(scope, [event])
    assert setup.worker.cycle()["counts"] == {"skipped": 1}
    assert setup.outbox.rows()[0]["detail"] == reason
    assert setup.outbox.rows()[0]["attempts"] == 0


def test_stale_sensor_suppresses_even_a_new_event(setup, monkeypatch):
    post(setup.client)
    original = setup.backend.get
    def stale(path, **params):
        result = original(path, **params)
        if path == "/api/sensors/status":
            result["SENSOR_01"]["data_status"] = "stale"
        return result
    monkeypatch.setattr(setup.backend, "get", stale)
    assert setup.worker.cycle()["counts"] == {"skipped": 1}
    assert setup.outbox.rows()[0]["detail"] == "current_data_not_fresh"


@pytest.mark.parametrize("source,expected", [("simulated", "simulated_live_disabled"), ("unknown", "unverified_source")])
def test_live_requires_explicit_simulation_opt_in_and_declared_source(setup, source, expected):
    post(setup.client, source=source)
    worker, sender = live_worker(setup)
    worker.allow_simulated = False
    assert worker.cycle()["counts"] == {"skipped": 1}
    sender.send.assert_not_called()
    assert setup.outbox.rows()[0]["detail"] == expected


def test_recovery_card_does_not_claim_road_is_safe(setup):
    post(setup.client, 1.7)
    setup.worker.cycle()
    post(setup.client, 0.1, offset=30)
    setup.worker.cycle()
    post(setup.client, 0.1, offset=60)
    assert setup.worker.cycle()["counts"] == {"dry_run": 2}
    assert "Recovery recorded" in setup.output[-1]
    assert "road safety is unverified" in setup.output[-1]


@pytest.mark.parametrize("bad", [[{"event_id": 1}], {"events": []}, [None],
                                     [{"event_id": True}], ["invalid"]])
def test_malformed_api_batch_fails_before_cursor_advances(setup, monkeypatch, bad):
    monkeypatch.setattr(setup.backend, "get", lambda *args, **kwargs: bad)
    with pytest.raises(BackendUnavailable, match="invalid_event_batch"):
        setup.worker.cycle()
    assert setup.outbox.rows() == []


@pytest.mark.parametrize("change", [{"created_at": None}, {"created_at": 123}, {"event_id": 2**63}])
def test_invalid_event_fields_defer_cycle_without_checkpointing(setup, monkeypatch, change):
    post(setup.client, 1.7)
    event = setup.client.get("/api/alerts").json()[0]
    with monkeypatch.context() as patch:
        patch.setattr(setup.backend, "get", lambda *args, **kwargs: [event | change])
        with pytest.raises(BackendUnavailable, match="invalid_event_batch"):
            setup.worker.cycle()
    assert setup.outbox.rows() == []
    assert setup.worker.cycle()["counts"] == {"dry_run": 1}


@pytest.mark.parametrize("code,body,expected,detail", [
    (200, {"ok": True, "result": {"message_id": 7}}, "delivered", "provider_accepted"),
    (200, {"ok": True, "result": {}}, "uncertain", "invalid_provider_ack"),
    (200, {"ok": True, "result": {"message_id": True}}, "uncertain", "invalid_provider_ack"),
    (200, {"ok": False}, "uncertain", "invalid_provider_ack"),
    (200, ["unexpected"], "uncertain", "invalid_provider_ack"),
    (200, None, "uncertain", "invalid_provider_ack"),
    (401, {"ok": False, "error_code": 401, "description": "secret"}, "failed", "provider_rejected_401"),
    (403, {}, "failed", "provider_rejected_403"),
    (302, {}, "failed", "provider_rejected_302"),
    (500, {}, "retry", "provider_unavailable"),
    (503, {"ok": False}, "retry", "provider_unavailable"),
    (429, {"ok": False, "parameters": {"retry_after": 17}}, "retry", "rate_limited"),
    (200, {"ok": False, "error_code": 429, "parameters": {"retry_after": "invalid"}}, "retry", "rate_limited"),
])
def test_telegram_response_classification_is_mocked_and_sanitized(code, body, expected, detail):
    session = Mock()
    response = session.post.return_value
    response.status_code = code
    if body is None:
        response.json.side_effect = ValueError("body contains secret")
    else:
        response.json.return_value = body
    sender = TelegramSender("123:secret", "-123", session)
    outcome = sender.send("prototype <plain text>")
    assert (outcome.status, outcome.detail) == (expected, detail)
    assert "secret" not in repr(outcome) + repr(sender)
    call = session.post.call_args.kwargs
    assert call["json"] == {"chat_id": "-123", "text": "prototype <plain text>"}
    assert call["allow_redirects"] is False and call["timeout"] == (3, 10)
    if code == 429:
        assert outcome.retry_after == 17


@pytest.mark.parametrize("error,status", [(requests.ConnectTimeout, "retry"),
    (requests.ReadTimeout, "uncertain"), (requests.ConnectionError, "uncertain")])
def test_transport_errors_never_expose_token_or_assume_delivery(error, status):
    session = Mock()
    session.post.side_effect = error("https://api.telegram.org/bot123:secret/sendMessage")
    outcome = TelegramSender("123:secret", "1", session).send("test")
    assert outcome.status == status
    assert "secret" not in repr(outcome)


@pytest.mark.parametrize("url", ["ftp://localhost", "http://user:password@localhost", "http://localhost/api",
                               "http://localhost?token=secret", "http://localhost#frag"])
def test_backend_url_rejects_paths_credentials_and_non_http(url):
    with pytest.raises(ValueError):
        BackendClient(url)


def test_backend_http_and_json_errors_are_sanitized():
    session = Mock()
    backend = BackendClient("http://127.0.0.1:8000", session=session)
    session.get.side_effect = requests.ConnectionError("secret")
    with pytest.raises(BackendUnavailable, match="backend_unreachable") as exc:
        backend.stream_id()
    assert "secret" not in str(exc.value)
    session.get.side_effect = None
    session.get.return_value.status_code = 503
    with pytest.raises(BackendUnavailable, match="http_error"):
        backend.stream_id()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {"status": "ok", "database": "ready"}
    with pytest.raises(BackendUnavailable, match="missing_stream_identity"):
        backend.stream_id()


def test_default_cli_does_not_construct_telegram_sender_even_with_credentials(setup, monkeypatch):
    monkeypatch.setenv("STREAM_TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("STREAM_TELEGRAM_CHAT_ID", "-123")
    monkeypatch.setattr("notifications.cli.TelegramSender", Mock(side_effect=AssertionError("live sender created")))
    setup.backend.close = lambda: None
    monkeypatch.setattr("notifications.cli.BackendClient", lambda _: setup.backend)
    post(setup.client)
    assert main(["--once", "--state-db", str(setup.outbox.path)]) == 0
    assert setup.outbox.rows()[0]["status"] == "dry_run"


def test_cli_live_needs_credentials_before_any_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STREAM_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("STREAM_TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--live", "--state-db", str(tmp_path / "delivery.db")])
    assert exc.value.code == 2
    assert "requires valid" in capsys.readouterr().err
    assert not (tmp_path / "delivery.db").exists()


def test_local_status_has_no_network_and_excludes_payload(tmp_path, monkeypatch, capsys):
    outbox = Outbox(tmp_path / "delivery.db")
    outbox.enqueue("demo:dry-run:console", [{"event_id": 1, "reason": "private payload"}])
    monkeypatch.setattr("notifications.cli.BackendClient", Mock(side_effect=AssertionError("network attempted")))
    assert main(["--status", "--state-db", str(outbox.path)]) == 0
    output = capsys.readouterr().out
    assert "pending" in output and "private payload" not in output


def test_cli_failure_exit_is_visible_and_keeps_state(setup, monkeypatch):
    setup.backend.close = lambda: None
    monkeypatch.setattr("notifications.cli.BackendClient", lambda _: setup.backend)
    sender = Mock()
    sender.send.return_value = Outcome("failed", "provider_rejected_403")
    monkeypatch.setattr("notifications.cli.TelegramSender", lambda *args: sender)
    monkeypatch.setenv("STREAM_TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("STREAM_TELEGRAM_CHAT_ID", "-123")
    post(setup.client)
    assert main(["--live", "--allow-simulated", "--state-db", str(setup.outbox.path)]) == 1
    assert setup.outbox.rows()[0]["status"] == "failed"


def test_legacy_entry_point_is_import_safe(monkeypatch):
    monkeypatch.setattr(requests.Session, "get", Mock(side_effect=AssertionError("import performed network I/O")))
    path = PROJECT_DIR / "04_notifications" / "telegram_bot.py"
    spec = importlib.util.spec_from_file_location("legacy_notification_entry", path)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_real_cli_dry_run_restart_from_arbitrary_directory(tmp_path):
    ledger = tmp_path / "state #demo" / "delivery.db"
    with running_backend(tmp_path, tmp_path / "backend.db", tmp_path / "server.log") as client:
        response = client.post("/api/telemetry", json={
            "node_id": "SENSOR_01", "water_depth_m": 1.8, "source": "simulated",
        })
        assert response.status_code == 200
        command = [sys.executable, "-B", str(BACKEND_DIR / "notify.py"), "--once",
                   "--base-url", str(client.base_url).rstrip("/"), "--state-db", str(ledger)]
        first = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert first.returncode == 0, first.stdout + first.stderr
        assert "DRY RUN - NO MESSAGE SENT" in first.stdout
        second = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "DRY RUN - NO MESSAGE SENT" not in second.stdout
    assert Outbox(ledger).rows()[0]["status"] == "dry_run"


@pytest.mark.parametrize("arguments", [["--poll-seconds", "nan"], ["--batch-size", "0"],
    ["--max-event-age", "-1"], ["--max-attempts", "21"], ["--retry-uncertain", "1"]])
def test_cli_rejects_invalid_options(arguments):
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2
