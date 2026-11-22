import logging
import math
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import PROJECT_DIR, Settings, load_settings
from .database import TelemetryDatabase
from .spatial import load_spatial
from .telemetry import TelemetryConflict, TelemetryReading, public_reading

logger = logging.getLogger(__name__)


THRESHOLDS = {
    "SENSOR_01": {
        "name": "Rachenahalli Lake Sluice Outlet",
        "lead_time": "35–45 mins",
        "yellow": 0.50,
        "orange": 1.00,
        "red": 1.50
    },
    "SENSOR_02": {
        "name": "Dasarahalli Mid-Reach Culvert",
        "lead_time": "15–20 mins",
        "yellow": 0.60,
        "orange": 1.10,
        "red": 1.60
    },
    "SENSOR_03": {
        "name": "Manyata Tech Park Boundary / Gate 4",
        "lead_time": "0–5 mins (IMMEDIATE)",
        "yellow": 0.80,
        "orange": 1.20,
        "red": 1.50
    }
}


def data_freshness(reading: dict, now: datetime, stale_after_seconds: int) -> dict:
    if not reading["received_at"]:
        return {"data_status": "unknown", "data_age_seconds": None}
    received = datetime.fromisoformat(reading["received_at"].replace("Z", "+00:00"))
    age = max(0.0, (now - received).total_seconds())
    if reading["source"] != "simulated" and reading["observed_at"]:
        observed = datetime.fromisoformat(reading["observed_at"].replace("Z", "+00:00"))
        age = max(age, (now - observed).total_seconds())
    return {
        "data_status": "stale" if age > stale_after_seconds else "fresh",
        "data_age_seconds": round(age, 1),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    database = TelemetryDatabase(settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.init_db()
        logger.info("Telemetry database ready: %s", database.path)
        yield

    application = FastAPI(
        title="STREAM: Stormwater Real-time Early Alert Model",
        description="Backend Ingestion & Early Warning API for Rachenahalli-Manyata Corridor",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.database = database
    application.state.settings = settings

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        # Non-finite JSON numbers must produce a usable 422, not a failure while
        # JSON-encoding the validation error itself.
        detail = jsonable_encoder(exc.errors(), custom_encoder={
            float: lambda value: value if math.isfinite(value) else str(value),
            ValueError: str,
        })
        return JSONResponse(status_code=422, content={"detail": detail})

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health")
    def health():
        try:
            database.check_ready()
        except (sqlite3.Error, OSError):
            logger.exception("Telemetry database readiness check failed")
            return JSONResponse(status_code=503, content={
                "status": "unhealthy", "database": "unavailable", "mode": "prototype",
            })
        return {"status": "ok", "database": "ready", "mode": "prototype"}

    @application.get("/")
    def serve_dashboard():
        dashboard_path = PROJECT_DIR / "03_dashboard" / "index.html"
        if not dashboard_path.is_file():
            raise HTTPException(status_code=404, detail="Dashboard index.html not found")
        return FileResponse(dashboard_path)

    @application.post("/api/telemetry")
    def ingest_telemetry(data: TelemetryReading):
        if data.node_id not in THRESHOLDS:
            raise HTTPException(status_code=400, detail=f"Unknown node_id: {data.node_id}")

        received_at = datetime.now(timezone.utc)
        if (data.observed_at is not None and data.source != "simulated"
                and data.observed_at > received_at + timedelta(minutes=5)):
            raise HTTPException(status_code=422, detail="observed_at is more than five minutes in the future")
        try:
            reading, created = database.store_telemetry(data, received_at)
        except TelemetryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        cfg = THRESHOLDS[data.node_id]
        level = "GREEN"
        if data.water_depth_m >= cfg["red"]:
            level = "RED"
        elif data.water_depth_m >= cfg["orange"]:
            level = "ORANGE"
        elif data.water_depth_m >= cfg["yellow"]:
            level = "YELLOW"

        return {
            **public_reading(reading),
            "status": "logged" if created else "duplicate",
            "node_id": data.node_id,
            "alert_level": level,
            "lead_time": cfg["lead_time"],
            "recorded_depth_m": data.water_depth_m,
            "lead_time_source": "unvalidated_assumption",
        }

    @application.get("/api/sensors/status")
    def get_sensor_status():
        readings = database.get_latest_telemetry()
        status_summary = {}
        now = datetime.now(timezone.utc)

        for r in readings:
            nid = r["node_id"]
            depth = r["water_depth_m"]
            cfg = THRESHOLDS.get(nid, {})

            level = "GREEN"
            if depth >= cfg.get("red", 1.5):
                level = "RED"
            elif depth >= cfg.get("orange", 1.0):
                level = "ORANGE"
            elif depth >= cfg.get("yellow", 0.5):
                level = "YELLOW"

            status_summary[nid] = {
                **public_reading(r),
                **data_freshness(r, now, settings.stale_after_seconds),
                "name": cfg.get("name", nid),
                "water_depth_m": depth,
                "rate_of_rise_cm_min": r["rate_of_rise_cm_min"],
                "battery_voltage": r["battery_voltage"],
                "alert_level": level,
                "lead_time": cfg.get("lead_time", "N/A"),
                "last_updated": r["timestamp"],
                "lead_time_source": "unvalidated_assumption",
            }
        return status_summary

    @application.get("/api/spatial/sensors")
    def get_sensor_geojson():
        return load_spatial(PROJECT_DIR / "01_spatial_data" / "sensors.geojson", "Point")

    @application.get("/api/spatial/bypass")
    @application.get("/01_spatial_data/manyata_bypass_1.6km.geojson", include_in_schema=False)
    def get_bypass_geojson():
        return load_spatial(PROJECT_DIR / "01_spatial_data" / "manyata_bypass_1.6km.geojson", "LineString")

    @application.get("/api/sensors/history/{node_id}")
    def get_sensor_history(node_id: str, limit: int = Query(default=120, ge=1, le=1000)):
        if node_id not in THRESHOLDS:
            raise HTTPException(status_code=404, detail=f"Unknown node_id: {node_id}")
        return [public_reading(row) for row in database.get_history(node_id, limit)]

    return application


app = create_app()
