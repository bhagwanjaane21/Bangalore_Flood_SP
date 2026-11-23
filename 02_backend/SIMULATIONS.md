# Phase 4: repeatable simulation scenarios

These are finite virtual-sensor sequences for exercising STREAM's APIs and alert
rules. They are not calibrated rainfall, flood-volume or travel-time models.
Every submitted observation is labelled `source: simulated`.

## One-command review demo

From the repository root, using the Python environment from the backend setup:

```powershell
python 02_backend/simulate.py --list
python 02_backend/simulate.py progressing-surge --speed 5 --keep-server
```

The second command starts an isolated backend at `http://127.0.0.1:8001/`, plays
the sequence, verifies the result, and leaves the dashboard available. Open the
printed URL during or after playback. Ctrl+C stops the owned server. Choose
`--port 8002` if necessary, or `--port 0` to select an available port automatically.

Playback is finite even with `--keep-server`: the completed server serves stored
data without generating additional readings. Those readings become stale normally
after the freshness timeout. Without `--keep-server`, the owned server closes as
soon as playback/verification finishes; stored data and reports remain available.

Open **Review replay** on the dashboard to inspect saved checkpoints and
notification previews while a managed run is recording or after it finishes.
An isolated dry-run worker processes each verified step. The simulator compares
its preview IDs with the backend's persisted transitions before marking the run
complete. The dashboard and replay both work with local assets after setup.

## Profiles and expected results

| Scenario | Sequence | Expected event history |
| --- | --- | --- |
| `normal` | Four stable observations per node. | No alert transitions. |
| `sustained-rise` | Six frames; upstream rises below depth thresholds. | SENSOR_01 YELLOW then ORANGE, both from sustained trend. |
| `progressing-surge` | Six frames; illustrative upstream-to-downstream rise. | Nine ordered escalations, ending with all nodes RED. |
| `recovery` | Baseline, Gate 4 RED, one low reading, another low reading. | RED escalation, then confirmed GREEN recovery; the first low reading holds RED. |
| `isolated-spike` | Five frames; one jump below the depth threshold, then baseline. | No sustained-rise warning. A critical-depth reading would still alert immediately. |
| `sensor-dropout` | Two baseline frames, then stop SENSOR_02 while updating the others. | No fabricated hazard transitions; SENSOR_02 becomes stale while the other two stay fresh. |

The sequence offsets are demo observation times, not geographic surge-arrival
predictions. Scenario definitions and expected results are in
`simulation/scenarios.py`; the runner checks the actual backend against them.

## Clock and speed semantics

- Frames are spaced 30 **simulated observation seconds** apart.
- `--speed 5` plays each 30-second interval in about six real seconds. Default
  speed is 30, approximately one real second per interval.
- Changing speed does not change the depths, observation intervals or calculated
  rise rates. The backend receives explicit UTC observation times.
- Very fast playback may pass through intermediate states between dashboard
  polls; every observation remains in persisted history and events.
- Receipt timestamps remain actual server time. The runner never backdates them
  or updates SQLite directly to fabricate stale data.
- Dropout waits for the actual freshness timeout plus a small margin; this pause
  is **not** divided by playback speed. Other nodes send real HTTP heartbeats.

For a shorter dropout demonstration:

```powershell
python 02_backend/simulate.py sensor-dropout --stale-after-seconds 10 --keep-server
```

The timeout is configurable from 1 to 300 seconds; default is 90. Non-dropout
profiles require at least 30 seconds to preserve the alert engine's sampling-gap
rules. Playback intervals must be shorter than the configured timeout. Other
Phase 3 rules use their documented defaults in an owned demo server.

## Isolation, replay and ownership

Each managed run gets a new UUID folder under:

```text
02_backend/data/simulation_runs/<run-id>/
    telemetry.db    # Fresh demo database, retained after completion
    backend.log     # Owned server output
    plan.json       # Scenario definition and expected transitions
    report.json     # Results, checkpoints, observations and event history
    review.db       # Recorded station snapshots and this run's dry-run notification ledger
```

Generated runs are gitignored. Replaying creates a new folder/database rather
than overwriting earlier runs or resetting the normal backend's database.
`--report-dir <directory>` changes the parent directory for new run folders.

The owned child backend uses the same Python interpreter as the runner. It clears
inherited `STREAM_*` overrides, selects its own database and injects a run UUID.
The runner verifies that UUID through `/health` before posting. An occupied port
or a different backend identity is a failure, not an invitation to write to it.
Only the process started by this run is stopped during cleanup.

To reopen a completed run, use the following in a new PowerShell terminal.
Replace `<run-id>` with the UUID printed by the simulation. The regular dashboard
uses the original receipt times, so old readings usually show as stale. Open
`/review` for the station evidence as it was recorded during playback.

```powershell
$runId = '<run-id>'
$env:STREAM_DB_PATH = (Resolve-Path "02_backend/data/simulation_runs/$runId/telemetry.db").Path
$env:STREAM_SIMULATION_RUN_ID = $runId
python 02_backend/run.py
```

The two overrides apply to this terminal. Close it or remove both environment
variables before using the ordinary monitoring database. Historical runs
created before the replay feature retain their original JSON reports.

## External server mode

For an already-running, **empty** prototype backend:

```powershell
python 02_backend/simulate.py normal --base-url http://127.0.0.1:8000 --speed 30
```

This explicitly writes simulated data to that backend. The runner checks health,
status, event history and per-node history before posting; any existing data
causes a refusal without resetting it. External servers must use the default
Phase 3 policy and a freshness timeout matching `--stale-after-seconds`.
Nonmatching rules can fail the expected-state checks. Prefer managed mode for
replays. An external server is never stopped by the runner.

## Preview, completion and cancellation

```powershell
python 02_backend/simulate.py sustained-rise --dry-run
python 02_backend/simulate.py recovery --speed 1000 --observed-start 2026-01-01T00:00:00Z
```

`--list` and `--dry-run` perform no network/database/report writes. A fixed
`--observed-start` makes logical timestamps repeatable across separate databases.
It must include an offset or `Z`.

Successful runs print `COMPLETED` and save `status: completed, verified: true`.
Checks include each expected state/kind, final freshness, exact event order,
distinct observation IDs, persisted counts and persisted payload values. Before
recording a checkpoint, each station's status is matched to the actual latest
submitted reading ID, observation time, depth, station, battery and source.
Failures print `FAILED`, return nonzero, and retain a failed report and partial data.
They are never silently presented as completed demonstrations.

Ctrl+C during playback stops new submissions, records cancellation, stops an
owned server and exits with code 130. Stopping `--keep-server` after a completed
playback keeps the report marked completed and exits normally. Remote stop or
database-reset APIs are not introduced in this checkpoint.

If the preview server exits unexpectedly after verification, the command returns
nonzero and records `preview_exit_code` in the report. The completed replay and
its verification result are preserved for reopening.

The old `continuous_sim.py` and `mock_nodes.py` entry points now delegate to a
finite `progressing-surge` run. Importing them has no runtime side effects.

## Tests

```powershell
python -B -m pytest 02_backend/tests 03_dashboard/tests -q
node --test 03_dashboard/tests/dashboard.test.cjs
```

Scenario tests exercise all profiles against the real application using a
controlled test clock. Separate CLI tests launch real servers, including a real
dropout wait with a short configured timeout. Isolation, replay, occupied ports,
external ownership, lost acknowledgments, cancellation and failed verification
are covered. Browser rendering and physical-device LAN access remain separate checks.
