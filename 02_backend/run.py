"""Start with `python 02_backend/run.py` from the repository root."""

import argparse

import uvicorn

from app.config import load_settings


def main():
    parser = argparse.ArgumentParser(description="Run the STREAM prototype backend")
    try:
        settings = load_settings()
    except ValueError as exc:
        parser.error(str(exc))
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    if not args.host.strip():
        parser.error("--host must not be empty")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
