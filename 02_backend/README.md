# STREAM backend: startup and telemetry APIs

Use Python 3.10 or newer. The checkpoint was tested with Python 3.13.6.
Run these PowerShell commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\02_backend\requirements.txt
.\.venv\Scripts\python.exe .\02_backend\run.py
```

Open the dashboard at <http://127.0.0.1:8000/>, API documentation at
<http://127.0.0.1:8000/docs>, or readiness check at
<http://127.0.0.1:8000/health>. Stop with Ctrl+C.

The launcher also works from `02_backend` as `python run.py`, or from another
directory when passed by absolute path. Use the same virtual-environment Python.
For direct Uvicorn use from the root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir .\02_backend
```

The old `uvicorn main:app` invocation inside `app/` is replaced by these commands.

## Database lifecycle

Startup creates `02_backend/data/telemetry.db` and its schema if missing. A fresh
checkout starts empty; it does not require a committed database or sample rows.
Data survives restart. Connections are closed after every operation, including
errors. An invalid database path or incompatible schema fails startup visibly.
Phase 1 databases are migrated on startup without removing their records. Old
timestamps have unknown timezone, so they remain available as legacy timestamps
with unknown freshness rather than being relabelled as UTC.
Phase 3 adds stored alert decisions, per-node recovery state and alert events.
Startup initializes the latest historical state without generating old alert
events; restarting preserves recovery progress and does not replay notifications.

The previously committed `02_backend/app/telemetry.db` is no longer tracked or
used by default. Its existing local copy is retained. If you need those records,
stop the server and explicitly select it with
`$env:STREAM_DB_PATH = 'app/telemetry.db'`. This selects it without copying or
deleting data. Remove the override to return to the new default database.

`GET /health` checks the expected telemetry columns and database access on every
request. It returns 200 with `status: ok`, `database: ready`, `mode: prototype`, or
503 if storage becomes unavailable. It does not establish fresh sensor readings
or validate flood predictions. If a DB disappears after startup, the check fails
without creating a replacement; restart to initialize a new DB intentionally.

## Configuration

Environment overrides are optional. `.env.example` lists supported variables;
`.env` files are **not automatically loaded**.

| Variable | Default | Meaning |
| --- | --- | --- |
| `STREAM_DB_PATH` | `data/telemetry.db` | Absolute path, or relative to `02_backend`, independent of working directory. Parent directories are created at startup. |
| `STREAM_HOST` | `127.0.0.1` | Listen address; can also be set by `--host`. |
| `STREAM_PORT` | `8000` | Port 1–65535; can also be set by `--port`. |
| `STREAM_STALE_AFTER_SECONDS` | `90` | Positive age limit for telemetry freshness. Simulated observations use real receipt age; other sources use both receipt and observation age. |
| `STREAM_RECOVERY_SAMPLES` | `2` | Distinct fresh observations required before lowering an alert. |
| `STREAM_RECOVERY_MARGIN_M` | `0.05` | Recovery depth margin below the current alert threshold; finite value in [0, 0.3). |
| `STREAM_TREND_MIN_SPAN_SECONDS` | `10` | Minimum observation-time span for a rise-rate warning; finite positive value. |
| `STREAM_CORS_ORIGINS` | `http://localhost:5500,http://127.0.0.1:5500` | Comma-separated frontend origins for separate development servers. Same-origin dashboard requests need no CORS configuration. |

Example: use a separate demo database and allow a frontend on port 5173:

```powershell
$env:STREAM_DB_PATH = 'data/rehearsal.db'
$env:STREAM_CORS_ORIGINS = 'http://localhost:5173'
.\.venv\Scripts\python.exe .\02_backend\run.py --port 8001
```

For a phone on the same LAN, bind with `--host 0.0.0.0` and open
`http://<laptop-LAN-IP>:8000/`. The frontend team must replace hardcoded telemetry
URLs using `127.0.0.1` with relative/configured API URLs for this to work. The
telemetry endpoints remain a local prototype without authentication.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\02_backend\requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest .\02_backend\tests -q
```

Tests use isolated databases and include real launcher subprocesses. They do not
send Telegram messages or modify the legacy sample database.

Phase 3 uses one alert engine for ingestion and status, with persisted reasons,
trend evidence, recovery hysteresis and `GET /api/alerts`. See
[the API contract and prototype rules](API.md). Notification delivery and scenario
controls remain separate checkpoints in `05_docs_and_reviews/backend_plan.md`.
This checkpoint makes no frontend changes and sends no external messages.
