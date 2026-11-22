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

from .alert_engine import AlertPolicy, SENSOR_RULES, data_freshness
from .config import PROJECT_DIR, Settings, load_settings
from .database import TelemetryDatabase
from .spatial import load_spatial
from .telemetry import TelemetryConflict, TelemetryReading, public_reading

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    database = TelemetryDatabase(settings.database_path, AlertPolicy(
        max_gap_seconds=settings.stale_after_seconds,
        recovery_samples=settings.recovery_samples,
        recovery_margin_m=settings.recovery_margin_m,
        trend_min_span_seconds=settings.trend_min_span_seconds,
    ))

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
        result = {"status": "ok", "database": "ready", "mode": "prototype"}
        if settings.simulation_run_id:
            result["simulation_run_id"] = settings.simulation_run_id
        return result

    @application.get("/")
    def serve_dashboard():
        dashboard_path = PROJECT_DIR / "03_dashboard" / "index.html"
        if not dashboard_path.is_file():
            raise HTTPException(status_code=404, detail="Dashboard index.html not found")
        return FileResponse(dashboard_path)

    @application.post("/api/telemetry")
    def ingest_telemetry(data: TelemetryReading):
        if data.node_id not in SENSOR_RULES:
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

        return {
            **public_reading(reading),
            **data_freshness(reading, received_at, settings.stale_after_seconds),
            "status": "logged" if created else "duplicate",
            "recorded_depth_m": reading["water_depth_m"],
        }

    @application.get("/api/sensors/status")
    def get_sensor_status():
        now = datetime.now(timezone.utc)
        return {
            row["node_id"]: {
                **public_reading(row),
                **data_freshness(row, now, settings.stale_after_seconds),
                "last_updated": row["timestamp"],
            }
            for row in database.get_latest_telemetry()
            if row["node_id"] in SENSOR_RULES
        }

    @application.get("/api/spatial/sensors")
    def get_sensor_geojson():
        return load_spatial(PROJECT_DIR / "01_spatial_data" / "sensors.geojson", "Point")

    @application.get("/api/spatial/bypass")
    @application.get("/01_spatial_data/manyata_bypass_1.6km.geojson", include_in_schema=False)
    def get_bypass_geojson():
        return load_spatial(PROJECT_DIR / "01_spatial_data" / "manyata_bypass_1.6km.geojson", "LineString")

    @application.get("/api/sensors/history/{node_id}")
    def get_sensor_history(node_id: str, limit: int = Query(default=120, ge=1, le=1000)):
        if node_id not in SENSOR_RULES:
            raise HTTPException(status_code=404, detail=f"Unknown node_id: {node_id}")
        return [public_reading(row) for row in database.get_history(node_id, limit)]

    @application.get("/api/alerts")
    def get_alerts(limit: int = Query(default=50, ge=1, le=1000),
                   node_id: str | None = None, after_id: int | None = Query(default=None, ge=0)):
        if node_id is not None and node_id not in SENSOR_RULES:
            raise HTTPException(status_code=404, detail=f"Unknown node_id: {node_id}")
        return database.get_alerts(limit, node_id, after_id)

    return application


app = create_app()
