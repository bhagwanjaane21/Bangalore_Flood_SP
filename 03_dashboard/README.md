# STREAM dashboard

The dashboard monitors three proposed stations between Rachenahalli Lake and Manyata Tech Park in Bengaluru. During a simulation, the readings and alerts come from the same backend used for manual telemetry.

## Open it locally

Start the backend from the project root with `python 02_backend/run.py`, then open [the dashboard](http://127.0.0.1:8000/). A fresh database displays three stations awaiting their first readings. For a guided demonstration with an isolated database, follow the [project workflow](../05_docs_and_reviews/project_workflow.md). **Review replay** in the navigation opens the scenario evidence page.

The dashboard, its styles, JavaScript, station map and chart are served locally by FastAPI. The optional **Street map** link opens OpenStreetMap in a separate browser tab.

## Read the dashboard

| Element | What it shows |
| --- | --- |
| Corridor overview | How many stations have fresh readings, how many of those are in alert, their highest depth and the declared reading source. A dash means the value is unavailable. |
| Station view | Select a station in the menu or on the map to focus its marker, chart, card and latest 20 alert transitions. The overview totals always describe the whole corridor. |
| Station map | The proposed locations from QGIS, the illustrative bypass, geographic scale and north direction. A fresh red Gate 4 reading reveals the original alternative paths. Stale data or recovery removes them. Road passability and the alignment await field verification. |
| Water depth | Up to 120 stored readings per station, plotted in metres against observation time in IST. Click the plot or use the time slider to inspect the nearest stored observation. The dots identify stations with a reading at that exact time. Select a station and tick **Depth thresholds** to compare its readings with the configured YELLOW, ORANGE and RED depths. Breaks identify long gaps, interruptions and changes of source. |
| Station readings | The latest depth, age, status and reason. Current actions appear with fresh readings. Expand **Station details** for the source, time, configured depth thresholds, trend, recovery evidence and observation quality notes. |
| Alert log | A newest-first history of changes between levels. Each entry identifies its station and the evidence when recorded. Notification previews are available in the scenario replay. |

Station rules come from `/api/sensors/catalog`; GIS points and lines load separately. An unavailable spatial layer leaves station readings, alerts and chart history available. The dated measurements and alert stream have independent error states. The chart uses the backend's configured gap limit unless a valid `STREAM_DASHBOARD_CONFIG.historyGapSeconds` override is set before the dashboard script. Accelerated simulation age follows the backend receipt clock.

## Team contributions

Vanshika Khadyal (`vanshika-exe`) contributed the bypass trace and hydrograph in [PR #1](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/1), the conditional Gate 4 path illustration in [PR #2](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/2), and the alert history and decision explanations in [PR #8](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/8). Her original commits remain in the project history. The mapped bypass measures about 0.99 km. The source also records a reported length of 1.6 km.

The screenshots and layout measurements in [review](review/) document the dashboard as it appeared at the 22 November checkpoint. The local redesign uses a different layout.

## Verify

From the project root, run the dashboard and visual behavior tests with Node.js and the API integration tests with Python.

```powershell
node --test 03_dashboard/tests/*.test.cjs
python -B -m pytest 02_backend/tests 03_dashboard/tests -q
```

The dashboard tests cover unknown and stale readings, partial coverage, independent API failures, recovery, station selection, overlapping alert requests, timestamp handling, chart gaps and historical explanations. The visual tests exercise the map and chart renderers. The Python integration tests start a real backend with an isolated database.
