# STREAM: Stormwater Telemetry & Real-Time Early Alert Model
> **An End-to-End IoT, GIS & ML-Powered Urban Flash Flood Early Warning Platform**  
> **Target Corridor:** Rachenahalli Lake – Dasarahalli – Manyata Tech Park – Nagavara Lake (Primary Drain 127, North Bengaluru)

---

## 🤖 AI Context & Team Handover Summary
*(Note to Teammates & AI Assistants: Paste this entire README into your AI coding assistant so it understands our exact problem domain, GIS coordinates, existing codebase, 3-month execution roadmap, and remaining build milestones without starting from scratch.)*

### 1. What We Are Building & Why It Matters
During extreme monsoon cloudbursts in North Bengaluru (benchmark: **90 mm/hr** intensity, generating **~9.5 million litres** of flood surge), stormwater rushes southward down an **~3.35 km natural valley trough** from **Rachenahalli Lake** toward **Manyata Tech Park / Thanisandra Main Road (SH104)** and **Nagavara Lake** (Primary Drain `OBJECTID 127`).

* **The Real-World Bottleneck:** Historically, an artificial constriction narrowed the primary Rajakaluve from ~33 ft down to ~6 ft at a sharp 90° bend near Thanisandra Main Road / Lodha Mirabelle / Gate 4 (`BBMP OBJECTID 187`). While civic authorities recently constructed an **~1.6 km concrete bypass canal** between Manyata Tech Park and Karle Town Centre to relieve local ponding, unattenuated storm surges still threaten low-lying access gates and downstream Nagavara culverts during intense downpours.
* **The Engineering Opportunity (The 45-Minute Window):** Because storm surge waves travel down the ~3.35 km channel at ~0.6 m/s, it takes **35 to 45 minutes** for an upstream overflow at Rachenahalli Lake to reach Manyata Gate 4. Currently, that 45-minute window is completely wasted—security teams, basement pump operators, and commuters only find out roads are drowning after vehicles are submerged.
* **Our Solution (STREAM):** An API-first, hardware-to-cloud early warning platform that monitors water stage at 3 GIS-verified checkpoints along the channel, calculates rate-of-rise and lead times, visualizes live surges on a Web GIS dashboard, and dispatches instant actionable alerts via Telegram.

---

## ✅ What We Have Completed So Far

1. **GIS & Spatial Twin (QGIS):**
   * Mapped the **Hebbal – Rachenahalli – Manyata – Nagavara** drainage corridor using OpenStreetMap, KSRSAC primary drain maps (`OBJECTID 127`), BBMP flood-vulnerable points (`OBJECTID 187, 184, 188, 190`), and Mapzen 30m DEM terrain slope data.
   * Digitized the **1.6 km Manyata–Karle bypass canal** (`01_spatial_data/manyata_bypass_1.6km.geojson`).
   * Locked and exported **3 strategic early-warning sensor checkpoints** (`01_spatial_data/sensors.geojson`).
2. **Backend Ingestion & Database (`02_backend/`):**
   * Built a **SQLite time-series database** (`database.py` -> `telemetry.db`) storing water depth (`m`), rate of rise (`cm/min`), battery voltage, and real-time timestamps.
   * Built a **FastAPI REST server** (`app/main.py`) with Pydantic schema validation (`POST /api/telemetry`), live status aggregation (`GET /api/sensors/status`), and GeoJSON serving (`GET /api/spatial/sensors`).
   * Implemented the **3-tier cascade state machine** (`app/alert_engine.py`) classifying surges into `GREEN`, `YELLOW` (35–45 min lead), `ORANGE` (15–20 min lead), and `RED` (0–5 min immediate perimeter action).
3. **Virtual Hardware Telemetry Simulators (`02_backend/`):**
   * Built `mock_nodes.py` (step-by-step 90 mm/hr cloudburst test) and `continuous_sim.py` (continuous sine-wave storm surge simulator) using an **API-First / Digital Twin** approach so the entire software stack works without needing physical microcontrollers yet.
4. **Live Web GIS Operations Dashboard (`03_dashboard/index.html`):**
   * Built an interactive **Leaflet.js** web interface displaying the Bengaluru street map, our 3 GIS sensor coordinates, pulsing color-coded status markers, live water-depth cards, and lead-time countdowns.
5. **Automated Public & Ops Alerting (`04_notifications/telegram_bot.py`):**
   * Built a Telegram Bot polling dispatcher that detects threshold escalations and pushes formatted emergency action cards (water depth, rise rate, lead time, and barrier/pump instructions) directly to phones.

---

## 📂 What Is Inside This Codebase

```text
Bengaluru_Flood_STREAM/
├── 01_spatial_data/                  # Verified QGIS spatial exports
│   ├── sensors.geojson               # Exact lat/lon coordinates for SENSOR_01, 02, 03
│   ├── manyata_bypass_1.6km.geojson  # Digitized 1.6 km bypass canal alignment
│   └── maps/                         # Exported QGIS corridor & hotspot PNGs
├── 02_backend/                       # Core FastAPI server, DB & simulators
│   ├── app/
│   │   ├── main.py                   # FastAPI REST endpoints & CORS/static serving
│   │   ├── alert_engine.py           # Threshold & lead-time state machine
│   │   ├── database.py               # SQLite schema & telemetry logging helpers
│   │   └── telemetry.db              # Auto-generated local SQLite DB (gitignored)
│   ├── continuous_sim.py             # Continuous real-time storm wave generator
│   ├── mock_nodes.py                 # Single-run 90 mm/hr cloudburst test script
│   └── requirements.txt              # Python dependencies (fastapi, uvicorn, scikit-learn, etc.)
├── 03_dashboard/                     # Frontend Web GIS Command Center
│   ├── index.html                    # Leaflet map + live sensor gauge cards
│   ├── static/                       # CSS/JS assets
│   └── assets/                       # Icons/media
├── 04_notifications/                 # External alert dispatchers
│   └── telegram_bot.py               # Automated Telegram emergency alert bot
├── 05_docs_and_reviews/              # Project reports & mentor review tracking
│   ├── problem_statement.md
│   └── review_milestones.md
├── .gitignore                        # Excludes .venv, __pycache__, *.db, .env
└── README.md                         # Master project documentation (this file)

Sensor Checkpoints & Cascade Thresholds (sensors.geojson)
| Node ID | Location | Coordinates (Lat, Lon) | Trigger Threshold | Alert Level | Lead Time to Manyata |
|---|---|---|---|---|---|
| SENSOR_01 | Rachenahalli Lake Sluice Outlet | 13.05704, 77.61507 | Depth > 0.50 m | YELLOW (Advisory) | 35–45 mins |
| SENSOR_02 | Dasarahalli Mid-Reach Culvert | 13.05292, 77.61806 | Depth > 1.00 m or Rise > 10 cm/5m | ORANGE (Warning) | 15–20 mins |
| SENSOR_03 | Manyata Gate 4 / Bypass Intake | 13.04851, 77.61831 | Depth > 1.50 m (Freeboard critical) | RED (Critical Action) | 0–5 mins (Immediate) |
🏗️ System Architecture (The 4 Pillars)
[ Field Layer: IoT / Simulator ]   ──► Virtual Daemon (now) / ESP32 + JSN-SR04T Ultrasonic (Month 3)
                                       Posts JSON stage & rise rate every 30s over HTTP POST
                                                 │
                                                 ▼
[ Core Engine: Ingestion & ML ]    ──► Python FastAPI + SQLite Time-Series DB + Cascade State Machine
                                       + Random Forest Nowcasting Classifier (Rainfall + Upstream Stage)
                                                 │
                                                 ▼
[ Front-End: Web Operations Hub ]  ──► Live Leaflet.js Web GIS Dashboard (sensors, 1.6 km bypass layer,
                                       live Chart.js hydrographs, lead-time clocks & safe detours)
                                                 │
                                                 ▼
[ Public Layer: Direct Alerts ]    ──► Automated Telegram Bot + Cyber-Physical Actuator Simulator
                                       (Gate 4 pneumatic barriers & basement dewatering pumps)

🗺️ 3-Month Execution Roadmap & Mentor Review Milestones
We follow an API-First / Digital Twin strategy: build and validate the entire software, ML, dashboard, and alerting pipeline using simulated storm hydrographs first, then plug in physical ESP32 hardware without changing a single line of backend code.
Month 1: Foundation, Ingestion Pipeline & Alerting (Current Phase — Core Complete)
 * Week 1–2: Finalize QGIS spatial corridor (sensors.geojson, manyata_bypass_1.6km.geojson) and build FastAPI REST ingestion (POST /api/telemetry) + SQLite time-series storage (database.py).
 * Week 3: Build virtual sensor daemons (mock_nodes.py, continuous_sim.py) simulating 30–90 mm/hr storm surges and connect the Telegram Bot dispatcher (telegram_bot.py).
 * Week 4 — Mentor Review 1 Deliverable: Demonstrate live simulated storm waves cascading from SENSOR_01 \rightarrow SENSOR_03, updating the Leaflet Web GIS map in real time and firing automated Telegram emergency cards.
Month 2: Machine Learning Nowcasting & Dashboard Upgrades
 * Week 5: Build 02_backend/app/ml_predictor.py — train an interpretable Random Forest / XGBoost classifier (scikit-learn) inspired by flash-flood nowcasting research (using upstream water depth, rise rate \Delta h/\Delta t, terrain slope, and rainfall intensity mm/hr to predict inundation probability 0 or 1 in <2 seconds). Expose GET /api/predict.
 * Week 6: Add historical time-series endpoint (GET /api/sensors/history/{node_id}) to main.py and render manyata_bypass_1.6km.geojson directly onto the Leaflet map.
 * Week 7: Upgrade 03_dashboard/index.html with live Chart.js hydrograph curves, ML flood risk percentage badges, and Safe Detour Routing overlays (highlighting safe exits toward Bellary Road / Hebbal Flyover when SENSOR_03 turns Red).
 * Week 8 — Mentor Review 2 Deliverable: Demonstrate predictive ML nowcasting + live hydrographs + safe commuter detour routing during a simulated 90 mm/hr cloudburst.
Month 3: Physical IoT Hardware Prototype, Cyber-Physical Control & Field Validation
 * Week 9: Build 02_backend/actuator_sim.py — a cyber-physical defense simulator that automatically triggers Gate 4 pneumatic barriers and basement dewatering pumps when SENSOR_03 enters RED alert.
 * Week 10: Assemble 1 physical IoT prototype node (06_hardware_esp32/):
   * Microcontroller: ESP32 DevKit (~₹450)
   * Sensor: JSN-SR04T Waterproof Ultrasonic Transducer (~₹400) with moving-median noise filter
   * Connectivity & Housing: Wi-Fi / SIM800L GSM (~₹400) inside an IP65 waterproof junction box (~₹2,500–₹3,500 total)
   * Firmware Formula: h = H_{\text{mount}} - d_{\text{measured}}, posting JSON payloads directly to /api/telemetry.
 * Week 11: Bench-test and field-test the physical node over a water tank / campus culvert for 48 hours to validate sensor stability and battery/solar power.
 * Week 12 — Final Mentor Review Deliverable: Full hardware-to-cloud end-to-end demonstration: raising water level under the physical ultrasonic sensor triggers live dashboard hydrographs, ML flood risk alerts, simulated pump/barrier actuation, and instant Telegram warnings.
👥 Team Module Ownership Matrix
To work in parallel without blocking each other or causing merge conflicts, each teammate owns an independent module:
| Role / Owner | Primary Files | Responsibilities |
|---|---|---|
| Backend & ML Lead | 02_backend/app/main.py
02_backend/app/ml_predictor.py
02_backend/app/database.py | FastAPI endpoints, SQLite time-series queries, Random Forest nowcasting model (GET /api/predict), and API validation. |
| Frontend & GIS Dashboard Lead | 03_dashboard/index.html
03_dashboard/static/ | Leaflet map layers (rendering manyata_bypass_1.6km.geojson), Chart.js live hydrographs, ML risk UI, and safe detour routes. |
| Simulation, Alerts & Hardware Lead | 02_backend/continuous_sim.py
02_backend/actuator_sim.py
04_notifications/telegram_bot.py
06_hardware_esp32/ | Storm surge simulators, Telegram alert formatting, pump/barrier actuator simulation, and ESP32 + JSN-SR04T firmware. |
| Spatial Data & Validation Lead | 01_spatial_data/
05_docs_and_reviews/ | QGIS corridor maps, empirical data alignment (BBMP/KSRSAC/WELL Labs), field verification logs, and monthly review decks. |
📚 Research & Empirical Benchmarks Grounding This Project
 * BBMP & KSRSAC Spatial Audits: Verified Primary Drain OBJECTID 127 (~3.35 km valley reach) and flood-vulnerable hotspots OBJECTID 187 (Manyata / Thanisandra Main Road historic 33 ft \rightarrow 6 ft constriction), OBJECTID 184 (Kariyannapalya ORR Bridge), OBJECTID 188 (Esteem Mall / NH44), and OBJECTID 190 (Rachenahalli Lake inlet).
 * 2026 Bypass Infrastructure Reality: Accounts for the newly constructed ~1.6 km concrete bypass canal between Manyata Tech Park and Karle Town Centre, shifting our focus to monitoring residual surge velocity and downstream Nagavara confluence backwater risks.
 * WELL Labs (Bengaluru Manyata Flood Study): Uses the 90 mm/hr peak cloudburst benchmark (~9.5 million litres surge volume in the Manyata cluster) and models how upstream Nature-Based Solutions (bioswales, retention trenches) attenuate peak volume by ~48–50%.
 * Interpretable ML Flood Nowcasting (Computers, Environment and Urban Systems): Validates using fast ensemble tree classifiers (Random Forest / XGBoost) over slow fluid-dynamic PDE solvers (SWMM/HEC-HMS) to achieve <2 second real-time urban flash-flood hotspot prediction.

