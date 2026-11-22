"""Compatibility entry point; defaults to durable dry-run notification delivery."""

import sys
from pathlib import Path

# Keep the original command usable from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02_backend"))
from notifications.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
