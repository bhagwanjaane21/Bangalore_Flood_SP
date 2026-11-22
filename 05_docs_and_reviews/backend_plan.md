# STREAM backend plan

Prepared: 22 November 2026. Prototype review: 25 November 2026.
Owner: Vernit. Working branch: `backend/vernit`.
Baseline: `origin/main` at `c7a2e6f50767bb4a5b3c9f1769fc15806162f3d1`.

## Scope

Keep the approved STREAM project: three virtual checkpoints on the
Rachenahalli–Manyata corridor, telemetry ingestion, a GIS dashboard, flood
alerts, and a reproducible software demonstration. Physical hardware is not
required for this review. Frontend implementation stays with the frontend team.

Finish a reliable backend prototype before adding optional features. Retain
the current repository layout. Checkpoints 0, 1, 2, 3, 4 and 5 are complete locally; later
checkpoints remain the proposed implementation sequence.

## Checkpoints and completion evidence

| Checkpoint | Work | Complete when | Suggested commit |
| --- | --- | --- | --- |
| 0 — Review and plan | Review main and both open PRs; reproduce baseline failures; prepare this plan. | Findings and implementation checkpoints are recorded. | `docs: record backend plan and frontend PR review` |
| 1 — Reliable startup | Make backend imports and database paths independent of working directory; centralize configuration; document one startup command; provide health checks; ignore generated data and stop tracking the demo DB while preserving the schema initializer. | A fresh checkout starts with an empty generated DB; `/health` and `/docs` respond; existing API routes still work. | `fix: make backend startup and configuration reproducible` |
| 2 — Telemetry and frontend contract | Validate sensor IDs, finite nonnegative depths, timestamps and battery values; distinguish observation and receipt time; store source/provenance; add bounded history and bypass serving. | Invalid payloads are rejected; valid data persists across restart; history is chronological and survives refresh; both spatial routes work. | `feat: add validated telemetry history and spatial APIs` |
| 3 — One alert engine | Use one implementation for ingestion and status; calculate signed rise/fall trends from stored readings; explain alerts; separate data freshness from last observed hazard; record alert transitions and add recovery hysteresis. | Depth boundaries, rapid rises, recovery, missing data and stale data behave predictably; a local critical depth is never held back waiting for other sensors. | `feat: centralize alerts with trend and freshness checks` |
| 4 — Repeatable simulation | Replace the uncontrolled sine-wave demo with named, finite scenarios: normal, progressing surge, recovery, isolated spike and sensor dropout. Make base URL and speed configurable; label accelerated time. | Each scenario has documented expected results, can be replayed without manual database edits, and has a clear completion signal. | `feat: add reproducible flood demonstration scenarios` |
| 5 — Notifications | Repair the dispatcher; use environment configuration; default to dry-run without credentials; read backend alert events; handle retries and avoid treating failed delivery as success. | Mocked delivery and failure tests pass; dry-run shows the expected action card once per intended transition. Live delivery is checked only against an explicitly selected demo destination. | `fix: make alert dispatch observable and repeatable` |
| 6 — Integration and handover | Integrate with the team's combined frontend, verify phone/LAN access, document setup and API examples, rehearse scenarios and record results. | A teammate can clone, start, replay and explain the prototype using the written instructions. No unresolved defect breaks the main demonstration. | `test: verify prototype integration and document demo` |

After each checkpoint: run relevant tests, update the progress ledger below,
and make one focused commit. Fix new failures before starting the next checkpoint.
Do not run a full test suite repeatedly without a new change or unresolved risk.

## Schedule

- **22 November:** finish checkpoint 0, then checkpoint 1 and publish the proposed
  API contract for the frontend team's use. Start checkpoint 2.
- **23 November:** finish telemetry/history and the common alert engine.
- **24 November:** finish scenarios and notification dry-run; integrate the
  frontend; only then consider stretch work.
- **25 November:** rehearse the already working demo and fix blockers. Avoid
  adding new features immediately before the review.

These are target checkpoints, not guarantees. If time slips, prioritize
checkpoints 1–4 and 6; keep notification delivery in dry-run if necessary.

## Frontend API contract to implement

Preserve existing paths and keys wherever possible. New fields are additive.
Coordinate any different response shape before implementing it.

| Endpoint | Purpose / agreed shape to target |
| --- | --- |
| `GET /health` | Backend/database readiness and prototype mode. |
| `GET /api/spatial/sensors` | Existing GeoJSON sensor catalog; source of expected sensor IDs and coordinates. |
| `GET /api/spatial/bypass` | Bypass GeoJSON; distinguish missing data from a valid layer. Geometry must come from the GIS team, not invented coordinates. |
| `GET /01_spatial_data/manyata_bypass_1.6km.geojson` | Narrow compatibility alias for the URL used by both current frontend PRs. |
| `POST /api/telemetry` | Preserve `node_id`, `water_depth_m`, optional `rate_of_rise_cm_min`, and `battery_voltage`; introduce optional timezone-aware `observed_at` and `source`. Store receipt time separately; backend trend is authoritative when enough data exists. |
| `GET /api/sensors/status` | Preserve the map keyed by sensor ID and existing depth, rise-rate, voltage, alert, lead-time and last-updated keys. Add observation ID/time, `data_status`, `alert_reason`, and provenance. |
| `GET /api/sensors/history/{node_id}?limit=120` | Bounded list of persisted readings, oldest first, with observation IDs and UTC timestamps. Cap the limit, for example at 1,000. Unknown IDs receive 404. |
| `GET /api/alerts?limit=50` | Persisted alert transitions with stable IDs, previous/new level, sensor, reason and event time; useful for review evidence and notification deduplication. |

### Compatibility and data semantics

- Existing clients can still send their original payloads. If observation time is
  omitted, use receipt time and record that it was assigned by the server.
- Store timezone-aware UTC times; display local time in the frontend. Validate
  ordering and define duplicate/out-of-order handling so delayed readings cannot
  silently replace the latest observation.
- Do not trust a client-provided rise rate as a verified observation. Derive trends
  over a defined interval; avoid a fabricated zero when there is insufficient data.
- An absent sensor in the legacy status map means no reading exists. The frontend
  must compare it with the sensor catalog and display no data, not zero depth.
- Keep the last observed `alert_level` separate from `data_status`. Stale readings
  cannot establish present safety. The frontend must show this distinction.
- History charts must use observation timestamps/IDs, preserve gaps, and avoid
  appending the same reading at every browser poll. Refresh reloads stored history.
- Frontend requests should use relative URLs or a configurable API base URL.
  `127.0.0.1` on a phone refers to the phone, not the backend laptop.
- Keep `lead_time` as a display-compatible string, but mark assumed lead times
  explicitly as unvalidated. Add a source field; do not present them as forecasts.
- Maintain simulator observation time and real receipt time separately when using
  accelerated scenarios. Freshness checks must not be confused by the demo clock.

## Alert behaviour to implement and test

Use existing depth thresholds as prototype configuration, recording their
unvalidated status. Express all rates in cm/min and document any new rate rules.
The README's midstream threshold differs from the code and must be reconciled
explicitly rather than silently mixed into the implementation.

Depth-based critical alerts remain immediate. Use persistence/hysteresis for
recovery and lower-severity trend rules, not to suppress a critical local level.
An isolated implausible reading can raise a quality warning; do not automatically
classify every sudden rise as noise. Label sensor faults as uncertain.

Tests should cover threshold boundaries, recovery without repeated chatter,
stale/no readings, rapid rise below a depth threshold, invalid inputs,
chronological history, duplicate/out-of-order handling, and notification failures.
Use an isolated temporary database and mock external messaging.

## Optional work after the complete prototype works

1. **Corridor surge interpretation:** identify an observed upstream-to-downstream
   rising sequence; expose an explanation and evidence quality. Describe this as
   a candidate contribution under evaluation, not a validated hydraulic forecast.
2. **Actuator simulation:** record intended pump/barrier states and manual
   overrides as software-only events, with explicit reset/recovery behaviour.
3. **ML experiment:** only if the team needs it and the core is stable. Define the
   future target and horizon, compare with a simple baseline, split by complete
   simulated storms, and label every metric as synthetic-data performance.
   Do not publish an arbitrary rule score as an ML probability.

The existing frontend's detour layer is illustrative. It cannot establish road
passability. Dynamic road routing, city-wide prediction, field-calibrated lead
times and physical actuation are outside the software prototype's completion claim.

## Branch and integration workflow

- Work locally in this workspace on `backend/vernit`, created from `origin/main`.
- Keep frontend source changes with the frontend team. Backend owns `02_backend/`,
  backend tests/configuration, and backend documentation. Coordinate ownership of
  `04_notifications/` before modifying it if another teammate is maintaining it.
- Do not merge teammate frontend PR branches into `backend/vernit` or implement
  their frontend features here. Review and merge those contributions through the
  teammate's original PRs into `main`; agree on frontend improvements separately.
  This branch may still contain all repository folders because a branch is a full
  project snapshot. Ownership is defined by its changes relative to `main`.
  Update from merged `main` normally; its inherited frontend work is not a new
  backend implementation change.
- Use the checkpoint commits above. Update this ledger with the commit and test
  result; each later implementation commit should include its ledger update.
- When publishing is requested, use `git push -u origin backend/vernit` and open a
  backend PR targeting `main`. If origin write access is unavailable, publish the
  same branch to a personal fork and target the original repository.
- Fetch upstream progress before integrating. Avoid force-pushing shared work.
- Frontend PRs need reconciliation; do not merge both unchanged. See the separate
  PR review. Test the combined frontend/backend before merging into `main`.

### Branch ownership correction — 22 November 2026

At the user's request, `backend/vernit` was returned to the backend-only Phase 2
checkpoint (`68da8f7`) before adding this scope clarification. The previously
combined frontend/backend prototype is preserved on the published
`integration/prototype` branch at `2771a6e`, including all original teammate
commits. No contribution has been discarded.

The original frontend PRs #1 and #2 remain open on GitHub. They have not been
merged into `main`. The backend branch changes no files in `01_spatial_data/`,
`03_dashboard/`, or `04_notifications/` relative to the reviewed main baseline.
The scope correction replaces the backend branch's published merge history using
an exact `--force-with-lease` check against the previously published head; the
integration branch preserves that head for anyone who already fetched it.

## Progress ledger

| Checkpoint | Status | Evidence |
| --- | --- | --- |
| 0 | Completed locally | Current repository and both PR heads reviewed; API smoke probes, mocked dispatcher probe, JS syntax checks and PR merge-tree check performed. See `frontend_pr_review.md`. |
| 1 | Completed locally | Package imports, central runtime settings, working-directory-independent launcher and paths, lifespan DB initialization, readiness checks, generated-data ignores, sample DB untracked, and setup instructions. All 20 tests passed on Python 3.13.6, including four real server launches (root/backend/unrelated directory/clean source copy), persistence, API compatibility and storage failure cases. |
| 2 | Completed locally | Validated finite telemetry, UTC observation/receipt timestamps, source labels, calculated signed rates, concurrent idempotent retries, explicit 409 conflicts, non-destructive legacy migration, bounded chronological history and validated spatial APIs. All 62 backend tests passed before frontend integration. See `02_backend/API.md`. |
| 3 | Completed locally | One persisted alert engine, signed trend evidence, recovery deadband/persistence, explicit quality/freshness metadata and bounded/cursor-based event history. Telemetry, state and events share one transaction; migration/restart do not replay events. See Phase 3 verification below. |
| 4 | Completed locally | Six finite scenarios, isolated managed demo servers/databases, explicit simulated clocks and real freshness waits, duplicate-safe HTTP retries, owned-process checks, cancellation, persisted plans/reports and exact expected-state/event/history verification. See Phase 4 verification below. |
| 5 | Completed locally (dry-run + mocked delivery) | Separate durable notification ledger, atomic event cursor, stream identity, claim/restart recovery, bounded retries, 429 cooldown, explicit uncertain delivery, freshness/source guards and dry-run default. 65 notification cases passed; no real Telegram messages sent. Live demo-destination acceptance remains unverified. See Phase 5 verification below. |
| 6 | In progress | Original frontend PRs #1 and #2 and supporting fixes PR #4 are merged into main. Backend/real-server API and dashboard behavior checks pass. Visual/LAN checks and scenario rehearsal remain pending. |

### Checkpoint 1 verification — 22 November 2026

- Command: `python -B -m pytest 02_backend/tests -q -p no:cacheprovider`.
- Result: **20 passed**. Tests use temporary databases and do not send messages.
- Tested runtime: FastAPI 0.135.2, Uvicorn 0.42.0, Pydantic 2.12.5,
  HTTPX 0.28.1, pytest 9.0.2, Python 3.13.6.
- Startup now uses `python 02_backend/run.py`; see `02_backend/README.md` for
  virtual environment setup, optional configuration and LAN binding.
- The legacy `02_backend/app/telemetry.db` is retained locally but removed from
  version control. Fresh source uses the generated `02_backend/data/telemetry.db`.
- Existing telemetry payloads, status response keys and alert thresholds are
  preserved. Data validation, history, bypass serving and alert logic changes
  remain in checkpoints 2 and 3; frontend source is unchanged.

### Checkpoint 2 verification — 22 November 2026

- Command: `python -B -m pytest 02_backend/tests -q -p no:cacheprovider`.
- Result before frontend integration: **62 passed**, including Phase 1 startup
  tests and Phase 2 invalid-input, concurrent retry, chronology, restart,
  migration, timestamp/freshness and malformed spatial-data cases.
- Unknown battery/rate values remain null. Legacy timestamps retain unknown
  timezone provenance. No old rows are deleted or relabelled as measured UTC.
- Bypass serving is implemented; real geometry remains pending until PR #1 is
  integrated. Tests verify the API with temporary mapped geometry.
- The API contract is documented in `02_backend/API.md`. Adaptive alerts,
  hysteresis, persisted alert events and dispatcher repair remain later work.

### Branch cleanup and checkpoint 3 — 22 November 2026

- Updated local `main` and `backend/vernit` to merged main `4b9b57e` before
  implementation. Frontend features were received through main, not reimplemented
  on the backend branch.
- Deleted completed `fix/frontend-integration` and obsolete `integration/prototype`
  branches locally and on GitHub. Removed the merged local PR #2 resolution branch.
  The earlier backup references in this document are historical; the completed
  prototype and original teammate commits are now preserved in main.
- Phase 3 changes are limited to backend code/tests/configuration and this progress
  log. Shared rules retain the API's original depth thresholds and explicitly
  document SENSOR_02 ORANGE as 1.10 m. Windowed rise rules are prototype choices.
- Checks cover boundary levels, sustained versus isolated rises, source/time gaps,
  stale evidence, recovery noise, concurrent retries, cursor ordering, transaction
  rollback, restart persistence and migration without retrospective alerts.
- Final verification: `python -B -m pytest 02_backend/tests 03_dashboard/tests -q
  -p no:cacheprovider` and `node --test 03_dashboard/tests/dashboard.test.cjs`.
- Result: **111 Python tests and 5 dashboard behavior tests passed**. This includes
  47 Phase 3 cases and all earlier backend/real-server frontend integration tests.
- Notification delivery remains Phase 5. The frontend is unchanged by this checkpoint.

### Checkpoint 4 — 22 November 2026

- Started from merged Phase 3 main `049ad97` on `backend/vernit`.
- Added `simulate.py` and `simulation/` with normal, sustained-rise,
  progressing-surge, recovery, isolated-spike and sensor-dropout profiles.
- Managed runs use fresh UUID directories/databases and a process-identity health
  handshake. They ignore inherited backend overrides and never reset existing data.
  External playback is explicit and requires an empty prototype backend.
- Playback speed affects wall time, not observation intervals or computed rates.
  Dropout uses real elapsed time and heartbeats for the remaining nodes.
- Replaced the old endless sine-wave and import-time posting scripts with
  guarded compatibility entry points for the finite progressing-surge profile.
- Reports verify levels, decision kinds, freshness, ordered events and persisted
  observation values. Failed/cancelled runs retain partial evidence and stop owned
  processes. No external notifications or frontend implementation changes were made.
- Setup, demo commands, policy assumptions and cancellation semantics are in
  `02_backend/SIMULATIONS.md`. The next backend checkpoint remains notification delivery.
- Final verification: **144 Python tests and 5 dashboard behavior tests passed**,
  including 33 simulation cases. Commands:
  `python -B -m pytest 02_backend/tests 03_dashboard/tests -q -p no:cacheprovider`
  and `node --test 03_dashboard/tests/dashboard.test.cjs`.

### Checkpoint 5 — 22 November 2026

- Started from merged Phase 4 main `8f3e6d5` on `backend/vernit`.
- Added `02_backend/notify.py` and the `notifications/` worker package. The legacy
  `04_notifications/telegram_bot.py` delegates to it, with no hardcoded token,
  LAN address or import-time polling. Frontend and spatial sources are unchanged.
- `/health` now includes a database-persistent `event_stream_id`. Current sensor
  status includes the latest `alert_event_id`, preventing replay of an old episode
  that happens to have the same colour. Existing readiness/telemetry keys remain.
- The notification SQLite ledger atomically saves event pages and ingestion
  cursors, then records provider attempts separately. Mode/destination namespaces
  separate dry-run previews from live delivery and survive worker restarts.
- Added bounded 5xx/connect-timeout backoff, persistent 429 cooldown, permanent
  failure states and claim recovery. Lost acknowledgements/interrupted live claims
  become uncertain and require explicit, scoped operator retry. No exactly-once
  provider-delivery guarantee is claimed.
- Eligibility checks skip expired, stale, superseded and unverified-source events.
  Live simulated notifications require explicit opt-in. Recovery cards do not
  establish road safety. Delivery never blocks telemetry ingestion.
- Dry-run is the default even with inherited credentials. Credential values and
  raw provider/network responses are not printed or saved. Local status and CLI
  failures are observable. See `02_backend/NOTIFICATIONS.md` for demonstration,
  retry/exit semantics, configuration and limitations.
- Final verification: **209 Python tests and 5 dashboard behavior tests passed**,
  including **65 notification cases**. Commands:
  `python -B -m pytest 02_backend/tests 03_dashboard/tests -q -p no:cacheprovider`
  and `node --test 03_dashboard/tests/dashboard.test.cjs`.
- Verification includes two simultaneous claims, migration without lost alert
  history, malformed/provider failure responses, and a real local-server CLI
  dry-run followed by restart without duplicate preview. External sends are
  prohibited in notification tests. **No real Telegram delivery was attempted**;
  an explicitly selected live demo destination remains a later integration check.
- This checkpoint is committed/published on `backend/vernit`; merging into `main`
  and any teammate frontend changes remain separate work.
