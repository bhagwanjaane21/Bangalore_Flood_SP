"""One-command isolated backend demos and explicit external-server playback."""

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.config import BACKEND_DIR
from app.telemetry import utc_timestamp
from .runner import HttpApi, ScenarioRunner, SimulationCancelled, SimulationError
from .scenarios import SCENARIOS


def positive_float(value):
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def port_number(value):
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535; 0 selects a free port")
    return number


def observation_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO 8601 timestamp with a timezone") from exc


def base_url(value):
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid URL port") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("use an HTTP(S) backend URL without credentials, query or fragment")
    return value.rstrip("/")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


class OwnedBackend:
    def __init__(self, folder: Path, run_id: str, port: int, stale_after_seconds: int):
        self.folder = folder
        self.run_id = run_id
        self.port = port
        self.stale_after_seconds = stale_after_seconds
        self.process = None
        self.log = None
        self.api = None

    def start(self):
        if self.port == 0:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                self.port = listener.getsockname()[1]
        self.api = HttpApi(f"http://127.0.0.1:{self.port}", timeout=.5)
        env = {key: value for key, value in os.environ.items() if not key.startswith("STREAM_")}
        env.update(STREAM_DB_PATH=str(self.folder / "telemetry.db"),
                   STREAM_SIMULATION_RUN_ID=self.run_id,
                   STREAM_STALE_AFTER_SECONDS=str(self.stale_after_seconds),
                   PYTHONDONTWRITEBYTECODE="1")
        self.log = (self.folder / "backend.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(BACKEND_DIR),
             "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log"],
            cwd=BACKEND_DIR, env=env, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SimulationError("Demo server exited during startup; see backend.log")
            try:
                health = self.api.health()
                if health.get("simulation_run_id") != self.run_id:
                    raise SimulationError("The port serves another backend; choose a different --port")
                if health.get("status") == "ok":
                    self.api.timeout = 5
                    return self.api
            except SimulationError as exc:
                if "another backend" in str(exc):
                    raise
            time.sleep(.05)
        raise SimulationError("Demo server startup timed out; see backend.log")

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.api is not None:
            self.api.close()
        if self.log is not None:
            self.log.close()


def main(argv=None, *, default_scenario=None):
    parser = argparse.ArgumentParser(description="Play and verify finite STREAM sensor scenarios")
    parser.add_argument("scenario", nargs="?", choices=tuple(SCENARIOS), default=default_scenario)
    parser.add_argument("--list", action="store_true", help="List scenarios without starting a server")
    parser.add_argument("--dry-run", action="store_true", help="Print the scenario plan; no network or database writes")
    parser.add_argument("--speed", type=positive_float, default=30., help="Logical seconds per real second (default: 30)")
    parser.add_argument("--port", type=port_number, default=8001, help="Owned demo server port; 0 selects a free port")
    parser.add_argument("--base-url", type=base_url, help="Use an already-running EMPTY backend instead of launching one")
    parser.add_argument("--keep-server", action="store_true", help="Keep the owned dashboard available after finite playback; Ctrl+C stops it")
    parser.add_argument("--stale-after-seconds", type=int, default=90, help="Demo freshness timeout (1–300); only dropout supports values below 30")
    parser.add_argument("--observed-start", type=observation_time, help="Optional deterministic logical start time")
    parser.add_argument("--report-dir", type=Path, help="Parent directory for a new run folder (default: backend data/simulation_runs)")
    args = parser.parse_args(argv)
    if args.list:
        for scenario in SCENARIOS.values():
            print(f"{scenario.name}: {scenario.description}")
        return 0
    if not args.scenario:
        parser.error("choose a scenario or use --list")
    if not 1 <= args.stale_after_seconds <= 300:
        parser.error("--stale-after-seconds must be between 1 and 300")
    if args.scenario != "sensor-dropout" and args.stale_after_seconds < 30:
        parser.error("non-dropout scenarios require --stale-after-seconds >= 30")
    if 30 / args.speed >= args.stale_after_seconds:
        parser.error("playback interval must be shorter than the freshness timeout; increase --speed")
    if args.base_url and args.keep_server:
        parser.error("--keep-server applies only to an owned demo server")
    if args.base_url and args.port != 8001:
        parser.error("--port applies only to an owned demo server")
    scenario = SCENARIOS[args.scenario]
    if args.dry_run:
        print(json.dumps(asdict(scenario), indent=2))
        return 0

    run_id = str(uuid4())
    parent = args.report_dir or BACKEND_DIR / "data" / "simulation_runs"
    folder = parent.expanduser().resolve() / run_id
    try:
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / "plan.json", asdict(scenario))
    except OSError as exc:
        parser.error(f"cannot create a new run report directory: {exc}")
    owned = None
    api = None
    runner = None
    cancel = threading.Event()
    report = {"run_id": run_id, "scenario": scenario.name, "status": "prepared", "verified": False}
    exit_code = 0
    try:
        if args.base_url:
            api = HttpApi(args.base_url)
        else:
            owned = OwnedBackend(folder, run_id, args.port, args.stale_after_seconds)
            api = owned.start()
        print(f"Run: {run_id} | scenario: {scenario.name} | source: SIMULATED", flush=True)
        print(f"Dashboard: {api.base_url}/ | report: {folder / 'report.json'}", flush=True)
        print(f"Playback speed: {args.speed:g}x; dropout freshness uses real elapsed time.", flush=True)
        runner = ScenarioRunner(scenario, api, run_id=run_id, speed=args.speed,
                                stale_after_seconds=args.stale_after_seconds,
                                observed_start=args.observed_start, cancel_event=cancel,
                                log=lambda line: print(line, flush=True))
        report = runner.run()
        report.update(base_url=api.base_url, backend_owned=owned is not None)
        write_json(folder / "report.json", report)
        print(f"COMPLETED: {len(report['reading_ids'])} readings, {len(report['events'])} alert events; all checks passed.", flush=True)
        if owned and args.keep_server:
            print("Playback finished. Dashboard remains available; Ctrl+C stops the server. Readings will become stale normally.", flush=True)
            try:
                while owned.process.poll() is None:
                    time.sleep(.25)
            except KeyboardInterrupt:
                print("Stopped demo server. Completed report retained.", flush=True)
            else:
                raise SimulationError("Demo server stopped unexpectedly after playback")
    except (KeyboardInterrupt, SimulationCancelled):
        cancel.set()
        report = runner.report if runner else report
        report.update(status="cancelled", verified=False)
        print("CANCELLED: partial report retained; no existing database was reset.", flush=True)
        exit_code = 130
    except Exception as exc:
        report = runner.report if runner else report
        report.update(status="failed", verified=False, error=str(exc))
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        exit_code = 1
    finally:
        if owned:
            owned.close()
        elif api:
            api.close()
        write_json(folder / "report.json", report)
    return exit_code
