"""Runtime configuration with paths independent of the launch directory."""

import os
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data" / "telemetry.db"


@dataclass(frozen=True)
class Settings:
    database_path: Path = DEFAULT_DATABASE_PATH
    host: str = "127.0.0.1"
    port: int = 8000
    stale_after_seconds: int = 90
    recovery_samples: int = 2
    recovery_margin_m: float = 0.05
    trend_min_span_seconds: float = 10
    cors_origins: tuple[str, ...] = (
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    )

    def __post_init__(self):
        if not self.host.strip():
            raise ValueError("STREAM_HOST must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("STREAM_PORT must be between 1 and 65535")
        if self.stale_after_seconds < 1:
            raise ValueError("STREAM_STALE_AFTER_SECONDS must be a positive integer")
        if self.recovery_samples < 1:
            raise ValueError("STREAM_RECOVERY_SAMPLES must be a positive integer")
        if not math.isfinite(self.recovery_margin_m) or not 0 <= self.recovery_margin_m < 0.3:
            raise ValueError("STREAM_RECOVERY_MARGIN_M must be finite and in [0, 0.3)")
        if not math.isfinite(self.trend_min_span_seconds) or self.trend_min_span_seconds <= 0:
            raise ValueError("STREAM_TREND_MIN_SPAN_SECONDS must be finite and positive")
        path = Path(self.database_path).expanduser()
        if not path.is_absolute():
            path = BACKEND_DIR / path
        object.__setattr__(self, "database_path", path.resolve())


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    database_path = Path(env.get("STREAM_DB_PATH") or DEFAULT_DATABASE_PATH).expanduser()
    try:
        port = int(env.get("STREAM_PORT", "8000"))
    except ValueError as exc:
        raise ValueError("STREAM_PORT must be an integer between 1 and 65535") from exc
    origins = env.get(
        "STREAM_CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500"
    )
    try:
        stale_after_seconds = int(env.get("STREAM_STALE_AFTER_SECONDS", "90"))
    except ValueError as exc:
        raise ValueError("STREAM_STALE_AFTER_SECONDS must be a positive integer") from exc
    try:
        recovery_samples = int(env.get("STREAM_RECOVERY_SAMPLES", "2"))
    except ValueError as exc:
        raise ValueError("STREAM_RECOVERY_SAMPLES must be a positive integer") from exc
    try:
        recovery_margin_m = float(env.get("STREAM_RECOVERY_MARGIN_M", "0.05"))
    except ValueError as exc:
        raise ValueError("STREAM_RECOVERY_MARGIN_M must be finite and in [0, 0.3)") from exc
    try:
        trend_min_span_seconds = float(env.get("STREAM_TREND_MIN_SPAN_SECONDS", "10"))
    except ValueError as exc:
        raise ValueError("STREAM_TREND_MIN_SPAN_SECONDS must be finite and positive") from exc
    return Settings(
        database_path=database_path,
        host=env.get("STREAM_HOST", "127.0.0.1"),
        port=port,
        stale_after_seconds=stale_after_seconds,
        recovery_samples=recovery_samples,
        recovery_margin_m=recovery_margin_m,
        trend_min_span_seconds=trend_min_span_seconds,
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
    )
