"""Notification CLI. Credentials are read only after explicit --live selection."""

import argparse
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path

from app.config import BACKEND_DIR
from .outbox import Outbox
from .transport import TelegramSender
from .worker import BackendClient, BackendUnavailable, Worker


def bounded_int(minimum, maximum):
    def parse(value):
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def positive_interval(value):
    number = float(value)
    if not math.isfinite(number) or not 0.1 <= number <= 60:
        raise argparse.ArgumentTypeError("must be between 0.1 and 60 seconds")
    return number


def parser():
    result = argparse.ArgumentParser(description="STREAM durable notification worker (default: dry-run)")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="explicitly enable Telegram delivery")
    mode.add_argument("--dry-run", action="store_true", help="print cards; never contact Telegram (default)")
    execution = result.add_mutually_exclusive_group()
    execution.add_argument("--once", action="store_true", help="one bounded cycle (default)")
    execution.add_argument("--watch", action="store_true", help="poll until Ctrl+C")
    execution.add_argument("--status", action="store_true", help="inspect local delivery records without network access")
    execution.add_argument("--retry-uncertain", type=bounded_int(1, 2**63 - 1), metavar="EVENT_ID",
                           help="explicitly retry an uncertain live event; duplicate delivery is possible")
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--state-db", type=Path, default=BACKEND_DIR / "data" / "notifications" / "delivery.db")
    result.add_argument("--poll-seconds", type=positive_interval, default=3)
    result.add_argument("--batch-size", type=bounded_int(1, 1000), default=100)
    result.add_argument("--max-attempts", type=bounded_int(1, 20), default=5)
    result.add_argument("--max-event-age", type=bounded_int(1, 86400), default=120,
                        help="maximum real seconds since event creation, not simulation observation time")
    result.add_argument("--allow-simulated", action="store_true",
                        help="allow simulated sources in explicit live mode; use only a demo destination")
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.retry_uncertain and not args.live:
        arguments.error("--retry-uncertain requires --live")
    token = chat_id = ""
    if args.live and not args.status:
        token = os.environ.get("STREAM_TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("STREAM_TELEGRAM_CHAT_ID", "").strip()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]+", chat_id):
            arguments.error("--live requires valid STREAM_TELEGRAM_BOT_TOKEN and STREAM_TELEGRAM_CHAT_ID")
    backend = sender = None
    try:
        state_path = args.state_db if args.state_db.is_absolute() else BACKEND_DIR / args.state_db
        outbox = Outbox(state_path)
        if args.status:
            # No payload, credentials, or chat IDs in the status output.
            fields = ("scope", "event_id", "status", "attempts", "detail", "due_at", "message_id")
            print(json.dumps([{key: row[key] for key in fields} for row in outbox.rows()], indent=2))
            return 0
        backend = BackendClient(args.base_url)
        sender = TelegramSender(token, chat_id) if args.live else None
        worker = Worker(backend, outbox, sender=sender, chat_id=chat_id,
                        allow_simulated=args.allow_simulated, max_event_age=args.max_event_age,
                        max_attempts=args.max_attempts, batch_size=args.batch_size)
        if args.retry_uncertain:
            _, scope = worker.identity()
            outbox.retry_uncertain(scope, args.retry_uncertain)
        print("STREAM notifications: " + ("LIVE demo delivery" if args.live else "DRY RUN; Telegram disabled"))
        while True:
            try:
                report = worker.cycle()
                print(json.dumps(report, sort_keys=True))
                unresolved = any(report["counts"].get(status, 0) for status in
                                 ("pending", "retry", "sending", "failed", "uncertain"))
                if not args.watch:
                    return 1 if unresolved or report["more_events"] else 0
            except BackendUnavailable as exc:
                print(f"Notification cycle deferred: {exc}")
                if not args.watch:
                    return 1
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Notification worker stopped; delivery records retained.")
        return 130
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        # Avoid printing raw network exceptions, provider responses or credentials.
        print("Notification worker stopped: storage/configuration failure. Check paths and --status.")
        return 1
    finally:
        if backend is not None:
            backend.close()
        if sender is not None:
            sender.close()


if __name__ == "__main__":
    raise SystemExit(main())
