import time
import requests

API_URL = "http://127.0.0.1:8000/api/telemetry"

# Simulating storm wave propagation down the 3.35 km corridor
simulation_steps = [
    # Baseline normal dry conditions
    {"node_id": "SENSOR_01", "water_depth_m": 0.20, "rate_of_rise_cm_min": 0.5},
    {"node_id": "SENSOR_02", "water_depth_m": 0.25, "rate_of_rise_cm_min": 0.4},
    {"node_id": "SENSOR_03", "water_depth_m": 0.15, "rate_of_rise_cm_min": 0.2},
    
    # 90 mm/hr Cloudburst hits: SENSOR_01 (Lake Sluice) surges (Yellow Alert)
    {"node_id": "SENSOR_01", "water_depth_m": 0.85, "rate_of_rise_cm_min": 8.5},
    
    # Wave reaches SENSOR_02 (Dasarahalli Culvert) (Orange Alert)
    {"node_id": "SENSOR_02", "water_depth_m": 1.25, "rate_of_rise_cm_min": 12.0},
    
    # Surcharge reaches SENSOR_03 (Manyata Gate 4) (Red Alert / Immediate Action)
    {"node_id": "SENSOR_03", "water_depth_m": 1.65, "rate_of_rise_cm_min": 15.0},
]

print(" Starting Storm Telemetry Simulation -> Ingesting to FastAPI...")
for step in simulation_steps:
    try:
        res = requests.post(API_URL, json=step, timeout=3)
        data = res.json()
        print(f"[{data['alert_level']} ALERT] {data['node_id']} | Depth: {data['recorded_depth_m']} m | Lead Time: {data['lead_time']}")
    except Exception as e:
        print(f"Error posting telemetry: {e}")
    time.sleep(2)

print("\n Simulation complete. All readings saved to telemetry.db.")