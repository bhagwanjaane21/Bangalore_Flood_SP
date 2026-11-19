import math
import time
from datetime import datetime
import requests

API_ENDPOINT = "http://127.0.0.1:8000/api/telemetry"

# Profile parameters for the 3 corridor checkpoints
SENSORS = {
    "SENSOR_01": {
        "name": "Rachenahalli Lake Sluice Outlet",
        "base_depth": 0.20,
        "peak_depth": 1.70,
        "phase_offset": 0.0,       # Initial wave front (T=0)
    },
    "SENSOR_02": {
        "name": "Dasarahalli Mid-Reach Culvert",
        "base_depth": 0.25,
        "peak_depth": 1.55,
        "phase_offset": 0.6,       # ~20 min lag behind SENSOR_01
    },
    "SENSOR_03": {
        "name": "Manyata Tech Park Boundary / Gate 4",
        "base_depth": 0.15,
        "peak_depth": 1.85,
        "phase_offset": 1.2,       # Surge arrival at Tech Park perimeter
    }
}

def run_continuous_simulation(cycle_minutes: float = 3.0, step_seconds: float = 2.0):
    """
    Simulates a continuous 90 mm/hr monsoon hydrograph wave.
    A full storm cycle runs over `cycle_minutes`, gradually surging
    from dry baseline -> YELLOW -> ORANGE -> RED -> receding.
    """
    print("=" * 65)
    print("🌊 STREAM Continuous Storm Hydrograph Simulator Initiated")
    print("Corridor: Rachenahalli Lake -> Dasarahalli -> Manyata Gate 4")
    print(f"Cycle Duration: {cycle_minutes} mins | Ingestion Interval: {step_seconds}s")
    print("Press CTRL + C anytime to halt.")
    print("=" * 65)

    step_count = 0
    total_steps = int((cycle_minutes * 60) / step_seconds)
    last_depths = {s: SENSORS[s]["base_depth"] for s in SENSORS}

    while True:
        # Normalized cycle progress (0.0 to 2*pi)
        progress = (step_count % total_steps) / total_steps
        angle = progress * 2 * math.pi

        for node_id, cfg in SENSORS.items():
            # Sine wave model adjusted to remain >= 0 (smooth crest and trough)
            wave = (math.sin(angle - cfg["phase_offset"]) + 1) / 2
            current_depth = cfg["base_depth"] + (cfg["peak_depth"] - cfg["base_depth"]) * wave

            # Calculate instantaneous rate of rise (cm/min)
            depth_delta_cm = (current_depth - last_depths[node_id]) * 100
            rate_of_rise = round((depth_delta_cm / (step_seconds / 60)), 1)
            last_depths[node_id] = current_depth

            payload = {
                "node_id": node_id,
                "water_depth_m": round(current_depth, 2),
                "rate_of_rise_cm_min": max(0.0, rate_of_rise),
                "battery_voltage": 3.7
            }

            try:
                res = requests.post(API_ENDPOINT, json=payload, timeout=2)
                if res.status_code == 200:
                    data = res.json()
                    lvl = data.get("alert_level", "GREEN")
                    emoji = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(lvl, "⚪")
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] {emoji} {node_id} | "
                        f"Depth: {payload['water_depth_m']:.2f} m | "
                        f"Status: {lvl:<6} | Lead Time: {data.get('lead_time', 'N/A')}"
                    )
            except Exception as e:
                print(f"❌ Error communicating with FastAPI: {e}")

        step_count += 1
        time.sleep(step_seconds)

if __name__ == "__main__":
    run_continuous_simulation(cycle_minutes=3.0, step_seconds=3.0)