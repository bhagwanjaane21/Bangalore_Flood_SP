import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import PROJECT_DIR, Settings, load_settings
from .database import TelemetryDatabase

logger = logging.getLogger(__name__)


class TelemetryReading(BaseModel):
    node_id: str
    water_depth_m: float
    rate_of_rise_cm_min: Optional[float] = 0.0
    battery_voltage: Optional[float] = 3.7


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

        database.log_telemetry(
            node_id=data.node_id,
            depth=data.water_depth_m,
            rise_rate=data.rate_of_rise_cm_min,
            voltage=data.battery_voltage
        )

        cfg = THRESHOLDS[data.node_id]
        level = "GREEN"
        if data.water_depth_m >= cfg["red"]:
            level = "RED"
        elif data.water_depth_m >= cfg["orange"]:
            level = "ORANGE"
        elif data.water_depth_m >= cfg["yellow"]:
            level = "YELLOW"

        return {
            "status": "logged",
            "node_id": data.node_id,
            "alert_level": level,
            "lead_time": cfg["lead_time"],
            "recorded_depth_m": data.water_depth_m
        }

    @application.get("/api/sensors/status")
    def get_sensor_status():
        readings = database.get_latest_telemetry()
        status_summary = {}

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
                "name": cfg.get("name", nid),
                "water_depth_m": depth,
                "rate_of_rise_cm_min": r["rate_of_rise_cm_min"],
                "battery_voltage": r["battery_voltage"],
                "alert_level": level,
                "lead_time": cfg.get("lead_time", "N/A"),
                "last_updated": r["timestamp"]
            }
        return status_summary

    @application.get("/api/spatial/sensors")
    def get_sensor_geojson():
        geojson_path = PROJECT_DIR / "01_spatial_data" / "sensors.geojson"
        if not geojson_path.is_file():
            raise HTTPException(status_code=404, detail="sensors.geojson not found")
        with geojson_path.open(encoding="utf-8") as f:
            return json.load(f)

    return application


app = create_app()
