# STREAM · Bengaluru flood monitoring

STREAM watches water depth at three proposed checkpoints between Rachenahalli Lake and Manyata Tech Park. A reviewer can follow a rise from the lake outlet to Gate 4, see which readings caused an alert, and inspect the notification the software prepared. The college prototype uses virtual sensors and local warning rules. Field measurements would be needed to establish flood arrival times or whether a road is passable.

## Run the review

From the repository root in PowerShell, using Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r 02_backend/requirements.txt
.\.venv\Scripts\python.exe 02_backend/simulate.py progressing-surge --speed 5 --keep-server
```

Open the [corridor dashboard](http://127.0.0.1:8001/) during playback. Use **Review replay** to step through the same run at your own pace, including after readings have aged. One terminal runs the backend, simulator, and notification worker in dry-run mode. After setup, the demo uses local assets and works offline. Ctrl+C stops this server. Use `--port 0` to choose an available port.

To receive your own readings, start `python 02_backend/run.py` and open the [monitoring dashboard](http://127.0.0.1:8000/). It starts with the contents of its own telemetry database. Each managed simulation uses a fresh, separate directory.

## How the pieces fit together

| Part | What it does |
| --- | --- |
| QGIS corridor data | Supplies the proposed station coordinates and a digitized bypass trace. |
| Virtual stations | Play six repeatable situations, including a gradual rise, recovery and a station going silent. |
| Backend and SQLite | Check inputs, store readings, calculate sustained rise and remember each station's warning level across restarts. |
| Live dashboard | Shows current coverage, water depths, the corridor map, historical trends and the reason for each change. Stations without fresh evidence have an unknown current condition. |
| Notification worker | Previews eligible level changes and keeps a delivery ledger. The isolated review workflow uses local dry runs. |
| Review replay | Retains each checked step with the three station decisions and actual preview cards from that step. Reviewers can revisit the evidence without injecting more readings. |

See the [project workflow and walkthrough](05_docs_and_reviews/project_workflow.md) for the flow diagram, demo script and expected results.

## Project references

- [Backend setup](02_backend/README.md) · [API reference](02_backend/API.md)
- [Simulation scenarios](02_backend/SIMULATIONS.md) · [Notification delivery](02_backend/NOTIFICATIONS.md)
- [Dashboard and teammate contributions](03_dashboard/README.md)
- [Original team proposal and research notes](05_docs_and_reviews/original_proposal.md) · [Earlier review handover](05_docs_and_reviews/demo_handover.md)

## Run the checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r 02_backend/requirements-dev.txt
.\.venv\Scripts\python.exe -B -m pytest 02_backend/tests 03_dashboard/tests -q
node --test "03_dashboard/tests/*.test.cjs"
```

The Python tests exercise real API requests, SQLite persistence, scenario playback, notification delivery with simulated providers, and saved replay evidence. The Node tests check dashboard and visual behavior. Use a current Node.js version with its built-in test runner.
