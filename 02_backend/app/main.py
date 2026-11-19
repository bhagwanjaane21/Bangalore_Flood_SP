import os
import json
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import init_db, log_telemetry, get_latest_telemetry

app = FastAPI(
    title="STREAM: Stormwater Real-time Early Alert Model",
    description="Backend Ingestion & Early Warning API for Rachenahalli-Manyata Corridor",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/")
def serve_dashboard():
    dashboard_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "03_dashboard", "index.html")
    )
    if not os.path.exists(dashboard_path):
        raise HTTPException(status_code=404, detail="Dashboard index.html not found")
    return FileResponse(dashboard_path)

@app.post("/api/telemetry")
def ingest_telemetry(data: TelemetryReading):
    if data.node_id not in THRESHOLDS:
        raise HTTPException(status_code=400, detail=f"Unknown node_id: {data.node_id}")
    
    log_telemetry(
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

@app.get("/api/sensors/status")
def get_sensor_status():
    readings = get_latest_telemetry()
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

@app.get("/api/spatial/sensors")
def get_sensor_geojson():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "01_spatial_data"))
    geojson_path = os.path.join(base_dir, "sensors.geojson")
    if not os.path.exists(geojson_path):
        raise HTTPException(status_code=404, detail=f"sensors.geojson not found at {geojson_path}")
    with open(geojson_path, "r", encoding="utf-8") as f:
        return json.load(f)