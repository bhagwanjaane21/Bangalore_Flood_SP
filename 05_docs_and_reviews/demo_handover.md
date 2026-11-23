# STREAM software demonstration and handover

**Historical checkpoint, 22 November.** The notes below record the earlier
two-terminal rehearsal and its browser results. For the current local dashboard,
offline map, one-command rehearsal and replay, use the [project workflow](project_workflow.md).

Prepared 22 November 2026 for the 25 November review. This is a software prototype
using virtual sensors. Hardware, calibrated forecasts and verified road routing
are outside this demonstration.

## Checkpoints and ownership

- Dashboard integration revision: `d91347d` (PR #10), following the original
  frontend PR #8 and fixes #9.
  Vanshika's original commit `9caa0d1` and authorship remain in main's history.
- Final dashboard checkpoint: `827278c`, merged through
  [PR #10](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/10).
  It adds readable data age/quality notes, responsive fixes, five regression
  cases and browser screenshots. Its temporary frontend branch can be removed;
  the checkpoint and screenshots remain in main and the PR history.
- This handover and rehearsal evidence are maintained on `backend/vernit`.
  That branch inherits already-merged frontend changes by syncing main, but its
  new checkpoint changes only backend documentation and review evidence.
- Use `main` for the final dashboard preview. The backend APIs and commands below
  are unchanged by these integration checkpoints.

## Setup

From the repository root in PowerShell, with Python 3.10+ and Node.js installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r 02_backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r 02_backend/requirements-dev.txt
```

Use the same virtual-environment interpreter in both terminals below. The tested
environment used Python 3.13.6 and Node.js 24. `.env` files are not auto-loaded.
No notification credentials are needed. Internet access supplies map tiles,
Leaflet and Chart.js; this is not an offline map bundle.

## Main presentation: two terminals

Terminal A — start the notification preview worker first:

```powershell
.\.venv\Scripts\python.exe 02_backend/notify.py --base-url http://127.0.0.1:8001 --watch --poll-seconds 0.5
```

It can report backend unavailable until terminal B starts. This command uses
dry-run even if credential variables exist. Do not add `--live` for this review.

Terminal B — start the isolated progressing-surge demonstration:

```powershell
.\.venv\Scripts\python.exe 02_backend/simulate.py progressing-surge --speed 5 --keep-server
```

Open **http://127.0.0.1:8001/** while playback runs. The six frames are separated
by six real seconds, giving time to see the transitions. The runner checks the
actual API result against its expected levels, events and stored observations.
At completion it prints a report location and leaves the server running.

Explain these points during the demo:

1. Three proposed checkpoints monitor an illustrative corridor. Depth thresholds
   and sustained rise can change the backend's stored alert decision.
2. The chart uses persisted observation times. The dashboard's data age comes
   from real freshness checks, not the accelerated observation clock.
3. Cards explain why a level is active. A low reading can still show RED while
   recovery is being checked. It takes further qualifying observations to clear.
4. Alert history is historical evidence, with source labels. Historical `fresh`
   does not establish current conditions. Simulated events are labelled prototype.
5. Notification cards say `DRY RUN - NO MESSAGE SENT`. The durable ledger prevents
   normal restart/re-poll duplication. Provider failures and uncertain outcomes
   have separate states; external exactly-once delivery is not promised.
6. Assumed travel windows are unvalidated. Alternative routes are illustrations;
   the system has not measured their passability or operated a pump/barrier.

Use Ctrl+C in terminal B to stop its owned server. Stop terminal A with Ctrl+C
when finished. Reports and databases are retained; do not delete a shared ledger
or overwrite a database to force a replay.

## Other scenarios

Stop terminal B's previous server before starting another profile on port 8001.
Terminal A may keep running: every fresh database has a distinct event-stream ID.

```powershell
.\.venv\Scripts\python.exe 02_backend/simulate.py normal --keep-server
.\.venv\Scripts\python.exe 02_backend/simulate.py sustained-rise --speed 5 --keep-server
.\.venv\Scripts\python.exe 02_backend/simulate.py recovery --speed 5 --keep-server
.\.venv\Scripts\python.exe 02_backend/simulate.py isolated-spike --speed 5 --keep-server
.\.venv\Scripts\python.exe 02_backend/simulate.py sensor-dropout --stale-after-seconds 10 --keep-server
```

Run these one at a time, not as a simultaneous batch. Dropout waits for actual
receipt age to exceed the timeout; playback speed does not shorten that wait.
Without continued readings, a completed demonstration eventually becomes stale.
That is expected: the last colour must not be presented as current safety.

## Recorded API and notification rehearsal

All six profiles were exercised through real HTTP against separate owned servers
and UUID databases. At each scenario checkpoint the dry-run worker read the event
API and current status. A restarted worker read the same ledger at completion.
No receipts were backdated and no database was reset to manufacture the result.
Dropout used a four-second timeout and real elapsed waiting; other profiles used
90 seconds, with playback speed 30.

| Profile | API events | Dry-run previews | New previews after worker restart | Final condition |
| --- | ---: | ---: | ---: | --- |
| normal | 0 | 0 | 0 | Three fresh GREEN nodes |
| sustained-rise | 2 | 2 | 0 | Upstream ORANGE from trend |
| progressing-surge | 9 | 9 | 0 | Three fresh RED nodes |
| recovery | 2 | 2 | 0 | Gate 4 recovers to GREEN |
| isolated-spike | 0 | 0 | 0 | No sustained-rise alert |
| sensor-dropout | 0 | 0 | 0 | SENSOR_02 stale; other nodes fresh |

Machine-readable summary: [phase6_rehearsal.json](phase6_rehearsal.json).
Detailed local reports are retained in the ignored directory
`02_backend/data/simulation_runs/handover-6ed28d47-25d1-49e1-8293-7699c8c64fea/`.
Each profile contains `report.json`, `notification_report.json`, its database and
server log. **13 previews, zero duplicate previews after restart, no real sends.**

## Verification and remaining checks

```powershell
.\.venv\Scripts\python.exe -B -m pytest 02_backend/tests 03_dashboard/tests -q -p no:cacheprovider
node --test 03_dashboard/tests/dashboard.test.cjs
.\.venv\Scripts\python.exe 02_backend/notify.py --status
```

The combined final dashboard/backend candidate passed **210 Python tests and
24 dashboard behavior tests**, now included in main through PR #10.
Tests cover empty/partial/stale status,
connection recovery, history isolation, configurable recovery requirements,
safe rendering, age/provenance and readable quality notes.

Actual browser checks loaded the map, chart and tiles, exercised empty startup,
sustained rise, held/confirmed recovery, connection loss and stale conditions,
and found no horizontal page/card overflow at 360, 390, 768, 800, 801 and 1366 px.
Screenshots and layout measurements ship with the frontend checkpoint under
`03_dashboard/review/`. The initial sandboxed server could not be reached by the
browser; the final visual checks used an isolated host-loopback server. All
owned servers were stopped after verification.

Still unverified: a physical second device on the LAN, an independent teammate
following setup from a clean clone, and optional live delivery to an explicitly
selected Telegram demo chat. Browser viewport tests do not establish phone/LAN
reachability. No firewall changes, public hosting or real notifications were used.

If a visual library fails to load, check internet access; cards can still show
API data. For an occupied port, choose a free port and use that same URL for the
worker/browser. For a failed scenario, keep its report and log rather than
silently restarting with the normal database. See [simulation details](../02_backend/SIMULATIONS.md)
and [notification semantics](../02_backend/NOTIFICATIONS.md).
