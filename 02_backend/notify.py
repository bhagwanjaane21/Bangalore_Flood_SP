"""Start the notification worker (dry-run unless --live is explicitly supplied)."""

from notifications.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
