"""Runtime configuration with paths independent of the launch directory."""

import os
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
    cors_origins: tuple[str, ...] = (
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    )

    def __post_init__(self):
        if not self.host.strip():
            raise ValueError("STREAM_HOST must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("STREAM_PORT must be between 1 and 65535")
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
    return Settings(
        database_path=database_path,
        host=env.get("STREAM_HOST", "127.0.0.1"),
        port=port,
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
    )
