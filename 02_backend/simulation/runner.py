"""Scenario playback and verification through the public HTTP APIs."""

import threading
import time
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from app.telemetry import utc_timestamp
from .scenarios import BASE_DEPTHS, NODES, Scenario


class SimulationError(RuntimeError):
    pass


class SimulationCancelled(SimulationError):
    pass


class HttpApi:
    def __init__(self, base_url: str, timeout: float = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

    def close(self):
        self.session.close()

    def _request(self, method, path, payload=None):
        # Observation timestamps are fixed before entering this retry loop.
        for attempt in range(3):
            try:
                response = self.session.request(method, self.base_url + path, json=payload, timeout=self.timeout)
                if response.status_code >= 500 and attempt < 2:
                    time.sleep(.1 * (attempt + 1))
                    continue
                if not 200 <= response.status_code < 300:
                    raise SimulationError(f"{method} {path}: HTTP {response.status_code}: {response.text[:200]}")
                return response.json()
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == 2:
                    raise SimulationError(f"Cannot reach backend for {method} {path}") from exc
                time.sleep(.1 * (attempt + 1))
            except requests.exceptions.JSONDecodeError as exc:
                raise SimulationError(f"{method} {path}: backend returned invalid JSON") from exc

    def health(self):
        return self._request("GET", "/health")

    def status(self):
        return self._request("GET", "/api/sensors/status")

    def ingest(self, payload):
        return self._request("POST", "/api/telemetry", payload)

    def history(self, node):
        return self._request("GET", f"/api/sensors/history/{node}?limit=1000")

    def alerts(self):
        return self._request("GET", "/api/alerts?limit=1000")


class ScenarioRunner:
    def __init__(self, scenario: Scenario, api, *, run_id: str, speed: float = 30,
                 stale_after_seconds: int = 90, observed_start: datetime | None = None,
                 cancel_event=None, monotonic=time.monotonic, wait=None, log=print, on_checkpoint=None):
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Playback speed must be finite and positive")
        if not 1 <= stale_after_seconds <= 300:
            raise ValueError("Freshness timeout must be between 1 and 300 seconds")
        if observed_start is not None and (observed_start.tzinfo is None or observed_start.utcoffset() is None):
            raise ValueError("Observation start time must have a timezone")
        self.scenario = scenario
        self.api = api
        self.speed = speed
        self.stale_after_seconds = stale_after_seconds
        self.observed_start = observed_start or datetime.now(timezone.utc)
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self.monotonic = monotonic
        self.wait = wait if wait is not None else self.cancel_event.wait
        self.log = log
        self.on_checkpoint = on_checkpoint
        self.reading_ids = []
        self.expected_observations = []
        self.expected_counts = Counter()
        self.report = {"run_id": run_id, "scenario": scenario.name, "status": "prepared",
                       "source": "simulated", "speed": speed,
                       "observed_start": utc_timestamp(self.observed_start),
                       "stale_after_seconds": stale_after_seconds, "checkpoints": [],
                       "expected_transitions": list(scenario.transitions)}

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise SimulationCancelled("Playback cancelled")

    def _wait_until(self, deadline):
        while self.monotonic() < deadline:
            self._check_cancelled()
            self.wait(min(.1, deadline - self.monotonic()))
        self._check_cancelled()

    def _send(self, node, depth, offset):
        self._check_cancelled()
        payload = {"node_id": node, "water_depth_m": depth, "battery_voltage": 3.7,
                   "source": "simulated", "observed_at": utc_timestamp(
                       self.observed_start + timedelta(seconds=offset))}
        reading = self.api.ingest(payload)
        if reading.get("status") not in ("logged", "duplicate") or reading.get("source") != "simulated":
            raise SimulationError("Backend did not acknowledge a simulated observation")
        if reading.get("observed_at") != payload["observed_at"]:
            raise SimulationError("Backend observation timestamp differs from the scenario clock")
        if reading.get("water_depth_m") != depth:
            raise SimulationError("Backend acknowledged a different water depth")
        self.reading_ids.append(reading["reading_id"])
        self.expected_observations.append({"reading_id": reading["reading_id"], **payload})
        self.expected_counts[node] += 1
        return reading

    def _checkpoint(self, label, offset, expected_levels, expected_kinds=None, expected_freshness=None):
        snapshot = self.api.status()
        actual = {node: snapshot.get(node, {}).get("alert_level") for node in NODES}
        checkpoint = {"label": label, "offset_seconds": offset,
                      "expected_levels": expected_levels, "actual_levels": actual,
                      "data_status": {node: snapshot.get(node, {}).get("data_status") for node in NODES}}
        self.report["checkpoints"].append(checkpoint)
        if actual != expected_levels:
            raise SimulationError(f"{label}: expected levels {expected_levels}, got {actual}")
        latest = {observation["node_id"]: observation for observation in self.expected_observations}
        for node, expected in latest.items():
            if any(snapshot[node].get(key) != value for key, value in expected.items()):
                raise SimulationError(f"{label}: {node} status differs from the latest observation")
        for node, kind in (expected_kinds or {}).items():
            if snapshot[node]["alert_kind"] != kind:
                raise SimulationError(f"{label}: {node} expected decision kind {kind}")
        for node, freshness in (expected_freshness or dict.fromkeys(NODES, "fresh")).items():
            if snapshot[node]["data_status"] != freshness:
                raise SimulationError(f"{label}: {node} expected {freshness} data")
        if self.on_checkpoint is not None:
            self.on_checkpoint(checkpoint, snapshot)
        self.log(f"CHECKPOINT {offset:g}s: {label} | " + ", ".join(f"{node}={actual[node]}" for node in NODES))
        return snapshot

    def run(self):
        start = self.monotonic()
        self.report.update(status="running", started_at=utc_timestamp(datetime.now(timezone.utc)))
        try:
            health = self.api.health()
            if health.get("mode") != "prototype" or health.get("status") != "ok":
                raise SimulationError("Backend must be a ready STREAM prototype")
            if self.api.status() or self.api.alerts() or any(self.api.history(node) for node in NODES):
                raise SimulationError("Backend contains existing data; use a fresh isolated demo database")
            final = None
            for step in self.scenario.steps:
                self._wait_until(start + step.offset_seconds / self.speed)
                for node in NODES:
                    self._send(node, step.depths[node], step.offset_seconds)
                final = self._checkpoint(step.label, step.offset_seconds, step.expected_levels, step.expected_kinds)
            if self.scenario.dropout_node:
                final = self._run_dropout()
            self._check_cancelled()
            events = self.api.alerts()
            actual_transitions = tuple((event["node_id"], event["new_level"], event["event_type"]) for event in events)
            self.report["events"] = events
            if actual_transitions != self.scenario.transitions:
                raise SimulationError(f"Unexpected transition sequence: {actual_transitions}")
            histories = {node: self.api.history(node) for node in NODES}
            counts = {node: len(rows) for node, rows in histories.items()}
            if counts != dict(self.expected_counts):
                raise SimulationError("Persisted history count differs from acknowledged scenario readings")
            if len(set(self.reading_ids)) != len(self.reading_ids):
                raise SimulationError("Scenario reused an observation instead of adding a distinct reading")
            persisted = {row["reading_id"]: row for rows in histories.values() for row in rows}
            for expected in self.expected_observations:
                if any(persisted.get(expected["reading_id"], {}).get(key) != value
                       for key, value in expected.items()):
                    raise SimulationError("Persisted reading differs from the acknowledged scenario payload")
            self.report.update(status="completed", verified=True, history_counts=counts,
                               expected_history_counts=dict(self.expected_counts),
                               final_status=final)
        except (SimulationCancelled, KeyboardInterrupt):
            self.report.update(status="cancelled", verified=False)
            raise
        except Exception as exc:
            self.report.update(status="failed", verified=False, error=str(exc))
            raise
        finally:
            self.report.update(finished_at=utc_timestamp(datetime.now(timezone.utc)),
                               elapsed_wall_seconds=round(self.monotonic() - start, 3),
                               reading_ids=list(self.reading_ids),
                               expected_observations=list(self.expected_observations))
        return self.report

    def _run_dropout(self):
        node = self.scenario.dropout_node
        duration = self.stale_after_seconds + .25
        self.log(f"DROPOUT: pausing {node} for {duration:g} real seconds (not divided by playback speed)")
        start = self.monotonic()
        offset = self.scenario.steps[-1].offset_seconds
        interval = min(1., self.stale_after_seconds / 3)
        while self.monotonic() < start + duration:
            self._wait_until(min(start + duration, self.monotonic() + interval))
            offset += 30
            for active_node in NODES:
                if active_node != node:
                    self._send(active_node, BASE_DEPTHS[active_node], offset)
        expected = dict.fromkeys(NODES, "fresh") | {node: "stale"}
        return self._checkpoint("Missing node is stale; other nodes remain fresh", offset,
                                self.scenario.steps[-1].expected_levels, expected_freshness=expected)
