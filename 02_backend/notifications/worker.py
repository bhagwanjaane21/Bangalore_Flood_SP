"""Read persisted transitions; queue before checkpointing; deliver only current alerts."""

import hashlib
import math
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import requests


class BackendUnavailable(RuntimeError):
    pass


class BackendClient:
    def __init__(self, base_url, session=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("base-url must be an HTTP(S) server origin without credentials or a path")
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False

    def get(self, path, **params):
        try:
            response = self.session.get(self.base_url + path, params=params, timeout=(2, 3),
                                        allow_redirects=False)
            if response.status_code != 200:
                raise BackendUnavailable("backend_http_error")
            return response.json()
        except (requests.RequestException, ValueError):
            raise BackendUnavailable("backend_unreachable_or_invalid_json") from None

    def stream_id(self):
        health = self.get("/health")
        if not isinstance(health, dict) or health.get("status") != "ok" or health.get("database") != "ready":
            raise BackendUnavailable("backend_not_ready")
        try:
            return str(uuid.UUID(health.get("event_stream_id")))
        except (ValueError, TypeError, AttributeError):
            raise BackendUnavailable("backend_missing_stream_identity") from None

    def close(self):
        self.session.close()


def scope_for(stream_id, *, live=False, chat_id=""):
    destination = hashlib.sha256(chat_id.strip().encode("utf-8")).hexdigest()[:20] if live else "console"
    return f"{stream_id}:{'live' if live else 'dry-run'}:{destination}"


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Missing timestamp timezone")
    return parsed.timestamp()


def skip_reason(event, current, now, *, max_age, live, allow_simulated):
    age = now - timestamp(event["created_at"])
    if age < -5:
        return "event_clock_in_future"
    if age > max_age:
        return "event_expired"
    if event["data_status_at_event"] != "fresh":
        return "event_data_not_fresh"
    if not isinstance(current, dict) or current.get("data_status") != "fresh":
        return "current_data_not_fresh"
    if current.get("alert_level") != event["new_level"] or current.get("source") != event["source"]:
        return "superseded"
    if current.get("alert_event_id") != event["event_id"]:
        return "superseded_transition"
    if type(current.get("reading_id")) is not int or current["reading_id"] < event["reading_id"]:
        return "current_reading_inconsistent"
    if live:
        if event["source"] == "simulated" and not allow_simulated:
            return "simulated_live_disabled"
        if event["source"] not in ("hardware", "manual", "simulated"):
            return "unverified_source"
    return None


def message_card(event, current, stream_id, *, dry_run):
    # Plain text needs no HTML/entity escaping. Bound free text well below Telegram's 4096 limit.
    clean = lambda value, size: " ".join(str(value).split())[:size]
    mode = "DRY RUN - NO MESSAGE SENT" if dry_run else "DEMO NOTIFICATION"
    action = ("Recovery recorded at this sensor. Continue monitoring; road safety is unverified."
              if event["new_level"] == "GREEN" else current.get("alert_action") or "Contact the designated operator.")
    return (
        f"STREAM PROTOTYPE | {mode}\n"
        f"{event['previous_level']} -> {event['new_level']} | {clean(event['node_id'], 64)}\n"
        f"Location: {clean(current.get('name', event['node_id']), 160)}\n"
        f"Event depth: {event['water_depth_m']} m\n"
        f"Reason: {clean(event['reason'], 400)}\n"
        f"Source: {clean(event['source'], 24)} | Current data: fresh\n"
        f"Observed (UTC): {clean(event.get('observed_at') or 'unknown', 40)}\n"
        f"Event created (UTC): {clean(event['created_at'], 40)}\n"
        f"Action: {clean(action, 400)}\n"
        f"Reference: {stream_id}/{event['event_id']}\n"
        "Prototype rules; no validated forecast or road-passability claim."
    )


def validate_events(events, cursor, batch_size):
    if not isinstance(events, list) or len(events) > batch_size:
        raise BackendUnavailable("invalid_event_batch")
    previous = cursor
    for event in events:
        try:
            if (not isinstance(event, dict) or type(event["event_id"]) is not int
                    or event["event_id"] <= previous or type(event["reading_id"]) is not int
                    or event["reading_id"] < 1 or not isinstance(event["node_id"], str)
                    or not isinstance(event["reason"], str)
                    or event["new_level"] not in ("GREEN", "YELLOW", "ORANGE", "RED")
                    or event["previous_level"] not in ("GREEN", "YELLOW", "ORANGE", "RED")
                    or event["source"] not in ("hardware", "manual", "simulated", "unknown", "legacy")
                    or event["data_status_at_event"] not in ("fresh", "stale", "unknown")):
                raise ValueError()
            timestamp(event["created_at"])
            depth = event["water_depth_m"]
            if type(depth) not in (int, float) or not math.isfinite(depth) or depth < 0:
                raise ValueError()
            previous = event["event_id"]
        except (KeyError, ValueError, TypeError, OverflowError):
            raise BackendUnavailable("invalid_event_batch") from None


class Worker:
    def __init__(self, backend, outbox, *, sender=None, chat_id="", allow_simulated=False,
                 max_event_age=120, max_attempts=5, batch_size=100, clock=time.time, emit=print):
        self.backend, self.outbox, self.sender = backend, outbox, sender
        self.live = sender is not None
        self.chat_id, self.allow_simulated = chat_id, allow_simulated
        self.max_event_age, self.max_attempts, self.batch_size = max_event_age, max_attempts, batch_size
        self.clock, self.emit = clock, emit

    def identity(self):
        stream_id = self.backend.stream_id()
        return stream_id, scope_for(stream_id, live=self.live, chat_id=self.chat_id)

    def check_identity(self, expected):
        if self.backend.stream_id() != expected:
            raise BackendUnavailable("backend_stream_changed; retry the cycle")

    def cycle(self):
        stream_id, scope = self.identity()
        cursor = self.outbox.cursor(scope)
        events = self.backend.get("/api/alerts", after_id=cursor, limit=self.batch_size)
        validate_events(events, cursor, self.batch_size)
        self.check_identity(stream_id)
        self.outbox.enqueue(scope, events)
        for _ in range(self.batch_size):
            item = self.outbox.claim(scope, self.clock(), live=self.live, max_attempts=self.max_attempts)
            if item is None:
                break
            try:
                statuses = self.backend.get("/api/sensors/status")
                if not isinstance(statuses, dict):
                    raise BackendUnavailable("invalid_sensor_status")
                self.check_identity(stream_id)
            except BackendUnavailable:
                # No provider call occurred. Safely release the claim without using an attempt.
                self.outbox.finish(scope, item, "retry", "backend_unavailable", due_at=self.clock() + 3)
                raise
            event = item["event"]
            current = statuses.get(event["node_id"])
            reason = skip_reason(event, current, self.clock(), max_age=self.max_event_age,
                                 live=self.live, allow_simulated=self.allow_simulated)
            if reason:
                self.outbox.finish(scope, item, "skipped", reason)
                self.emit(f"event={item['event_id']} skipped: {reason}")
                continue
            card = message_card(event, current, stream_id, dry_run=not self.live)
            self.outbox.start_attempt(scope, item)
            if not self.live:
                self.emit(card)
                self.outbox.finish(scope, item, "dry_run", "console_preview")
                continue
            outcome = self.sender.send(card)
            status, detail = outcome.status, outcome.detail
            attempt = item["attempts"] + 1
            delay = outcome.retry_after or min(60, 2 ** min(attempt, 6))
            if status == "retry" and attempt >= self.max_attempts:
                status, detail = "failed", "attempt_limit:" + detail
            self.outbox.finish(scope, item, status, detail, due_at=self.clock() + delay,
                               message_id=outcome.message_id, cooldown=outcome.detail == "rate_limited")
            self.emit(f"event={item['event_id']} status={status} detail={detail}")
        counts = {}
        for row in self.outbox.rows(scope):
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {"stream_id": stream_id, "mode": "live" if self.live else "dry-run",
                "cursor": self.outbox.cursor(scope), "counts": counts,
                "more_events": len(events) == self.batch_size}
