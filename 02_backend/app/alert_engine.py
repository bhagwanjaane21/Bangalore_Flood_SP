import json
import os
import time
from datetime import datetime

# --- CONFIGURATION & THRESHOLDS ---
GEOJSON_FILE = "sensors.geojson"

# Alert thresholds based on channel depth & rate of rise
THRESHOLDS = {
    "SENSOR_01": {
        "name": "Rachenahalli Lake Sluice Outlet",
        "lead_time": "35–45 mins",
        "yellow_depth": 0.50,  # meters above normal weir crest
        "orange_depth": 1.00,
        "red_depth": 1.50,
    },
    "SENSOR_02": {
        "name": "Dasarahalli Mid-Reach Culvert",
        "lead_time": "15–20 mins",
        "yellow_depth": 0.60,
        "orange_depth": 1.10,
        "red_depth": 1.60,
    },
    "SENSOR_03": {
        "name": "Manyata Tech Park Boundary / Gate 4",
        "lead_time": "0–5 mins (IMMEDIATE)",
        "yellow_depth": 0.80,
        "orange_depth": 1.20,
        "red_depth": 1.50,  # Near overtopping bank-full stage
    },
}

# Optional Telegram bot integration (leave empty strings to log to console only)
TELEGRAM_BOT_TOKEN = ""  # e.g., "123456789:ABCdefGhIJKlmNoPQRstuVWXyz"
TELEGRAM_CHAT_ID = ""    # e.g., "@manyata_flood_alerts" or private chat ID


def send_telegram_alert(message: str):
    """Dispatches real-time broadcast via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram alert: {e}")


def load_sensor_metadata(geojson_path: str) -> dict:
    """Extracts sensor IDs and geographic coordinates from QGIS GeoJSON."""
    if not os.path.exists(geojson_path):
        raise FileNotFoundError(f"Cannot find {geojson_path}. Ensure it is exported from QGIS.")

    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    sensors = {}
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        sensor_id = (
            props.get("Sensor_ID")
            or props.get("id")
            or props.get("Name")
        )
        coords = feature.get("geometry", {}).get("coordinates", [])
        if sensor_id:
            sensors[sensor_id] = {
                "lon": coords[0] if len(coords) > 0 else None,
                "lat": coords[1] if len(coords) > 1 else None,
            }
    return sensors


def evaluate_sensor_status(sensor_id: str, current_depth: float, rain_rate: float) -> dict:
    """Evaluates sensor readings against hydrological danger thresholds."""
    cfg = THRESHOLDS.get(sensor_id, {})
    name = cfg.get("name", sensor_id)
    lead_time = cfg.get("lead_time", "Unknown")

    if current_depth >= cfg.get("red_depth", 1.50):
        level = "RED"
        action = (
            "🚨 CRITICAL ACTION: Deploy pneumatic flood barriers at Gate 4. "
            "Start basement dewatering pumps immediately. Divert internal traffic away from Thanisandra road."
        )
    elif current_depth >= cfg.get("orange_depth", 1.00) or rain_rate >= 50.0:
        level = "ORANGE"
        action = (
            "⚠️ WARNING: High water surge confirmed moving down corridor. "
            "Clear low-lying access lanes. Advise commuters to use Bellary Road / Hebbal flyover exits."
        )
    elif current_depth >= cfg.get("yellow_depth", 0.50) or rain_rate >= 30.0:
        level = "YELLOW"
        action = (
            "📢 ADVISORY: Upstream lake surcharge detected. "
            "Security & facility teams on standby. Monitor 1.6 km bypass intake for debris blockages."
        )
    else:
        level = "GREEN"
        action = "Normal operating conditions. Drain freeboard clear."

    return {
        "sensor_id": sensor_id,
        "name": name,
        "level": level,
        "current_depth": current_depth,
        "lead_time": lead_time,
        "action": action,
    }


def trigger_alert_pipeline(status: dict):
    """Logs the warning and broadcasts to community channels."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    banner = "=" * 65
    print(f"\n{banner}")
    print(f"[{status['level']} ALERT] {status['name']} ({status['sensor_id']})")
    print(f"Timestamp: {timestamp} | Recorded Depth: {status['current_depth']:.2f} m")
    print(f"Estimated Lead Time to Manyata Flooding: {status['lead_time']}")
    print(f"Action Protocol:\n{status['action']}")
    print(f"{banner}\n")

    if status["level"] in ["YELLOW", "ORANGE", "RED"]:
        msg = (
            f"*{status['level']} ALERT - Manyata Flood Warning System*\n\n"
            f"📍 *Location:* {status['name']}\n"
            f"📏 *Water Depth:* {status['current_depth']:.2f} m\n"
            f"⏳ *Time to Impact:* {status['lead_time']}\n\n"
            f"📋 *Recommended Action:*\n{status['action']}\n\n"
            f"🕒 _Timestamp: {timestamp}_"
        )
        send_telegram_alert(msg)


# --- SIMULATION & RUNTIME TEST ---
if __name__ == "__main__":
    print("Loading sensor locations from QGIS GeoJSON...")
    try:
        nodes = load_sensor_metadata(GEOJSON_FILE)
        print(f"Successfully loaded {len(nodes)} sensor checkpoints from GeoJSON:")
        for s_id, geo in nodes.items():
            print(f"  • {s_id}: Latitude {geo['lat']}, Longitude {geo['lon']}")
    except Exception as err:
        print(f"Warning: {err}")

    print("\n--- INITIATING FLOOD SURGE SIMULATION ---")
    print("Simulating high-intensity cloudburst scenario (90 mm/hr monsoon storm)...\n")

    # Step 1: Upstream Rachenahalli Sluice triggers first
    time.sleep(1)
    status_01 = evaluate_sensor_status("SENSOR_01", current_depth=0.85, rain_rate=65.0)
    trigger_alert_pipeline(status_01)

    # Step 2: Surge travels mid-reach toward Dasarahalli 20 mins later
    time.sleep(2)
    status_02 = evaluate_sensor_status("SENSOR_02", current_depth=1.30, rain_rate=75.0)
    trigger_alert_pipeline(status_02)

    # Step 3: Surge hits Manyata Perimeter / Gate 4 intake
    time.sleep(2)
    status_03 = evaluate_sensor_status("SENSOR_03", current_depth=1.65, rain_rate=90.0)
    trigger_alert_pipeline(status_03)