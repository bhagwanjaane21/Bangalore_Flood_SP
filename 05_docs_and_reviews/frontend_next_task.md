# Next frontend task: alert history and explanations

Suggested owner: Vanshika / frontend teammate. Prepared for the team to share;
no message or GitHub assignment has been sent automatically.

## Goal

Make the Phase 3 backend's alert decisions understandable on the existing
dashboard: show what changed, why an alert is active, and when recovery is still
being checked. Keep the existing map, hydrograph and illustrative routes.

## Branch and ownership

1. Sync your fork's `main` after the Phase 3 backend PR has merged.
2. Create a new branch such as `feature/alert-timeline` from that updated main.
3. Work in `03_dashboard/`, including its tests and README as needed.
4. Open your own PR targeting `bhagwanjaane21/Bangalore_Flood_SP:main`.

Backend code and notification delivery remain with the backend owner. No
hardware, ML screen, pump controls or new route calculation is needed for this task.

## Priority 1: alert-history panel

Add a compact **Alert history** panel to the existing dashboard.

- Read `GET /api/alerts?limit=50` on initial load and in the existing polling
  cycle. The API returns oldest first; display newest first if clearer.
- Show sensor, previous/new alert level, escalation/recovery, reason, observation
  time and source. Use `event_id` as the stable identity.
- Replacing the list from the latest response is sufficient; do not append the
  same events at every poll. History must reload after a page refresh.
- A node filter can use `?node_id=SENSOR_01&limit=50` if time permits.
- Show an explicit empty state and a separate history-error state. A history
  request failure must not mark otherwise fresh current sensor data unavailable.
- Label these as historical transitions. `data_status_at_event` describes the
  evidence when the event was created; it is not the current sensor freshness.
- Do not add acknowledgment buttons: no backend acknowledgment API exists yet.

For later incremental loading, `after_id=<last_seen_id>&limit=50` returns the
next events in ascending order. This is optional for the current UI task; the
latest-50 refresh approach is simpler and already adequate for this prototype.

## Priority 2: explain the current sensor state

Extend existing cards using `GET /api/sensors/status`:

| Field | UI use |
| --- | --- |
| `alert_level` | Use the backend's GREEN/YELLOW/ORANGE/RED decision. Do not recalculate it from depth in JavaScript. |
| `alert_reason` | Show the backend explanation below the alert label. |
| `alert_kind` | Friendly label: depth, sustained rise, recovery being checked (`held`), or recovery confirmed. |
| `trend_rate_cm_min`, `trend_sample_count` | Show the supported trend and observation count when available; unknown is not zero. |
| `recovery_count` | Optional supporting count. Do not hardcode a target of two; configuration can change and the server reason already explains the requirement. |
| `data_status`, `data_warning` | Preserve the existing missing/stale/unknown handling and show a readable warning. |
| `alert_action` | Display as a current suggested response only when data is fresh. With stale/unknown data, show the data warning instead. |
| `source` | Keep simulated readings clearly labelled as simulated/prototype data. |
| `quality_flags` | Optional friendly evidence notes; avoid a raw dump of internal field names. An abrupt change is not proof of a faulty sensor. |

The backend may hold RED while the newest depth is lower, because recovery needs
repeated supporting readings. Show the stored level and reason faithfully.
Keep travel windows labelled as assumptions and route passability as unverified.

## Integration details

- Use relative URLs and the existing timeout/error handling.
- Extend the existing non-overlapping polling cycle instead of adding competing
  timers. Keep status, history and alert-history error states independent.
- Render API strings safely with text nodes or the existing escaping helper.
- Reuse the current layout and colours; make the new panel usable below 800 px.
- Rule-version identifiers are API/debug metadata, not necessary product labels.
- Full API reference: [`02_backend/API.md`](../02_backend/API.md).

## Acceptance checks

1. Empty database: three no-data cards and an empty alert-history message.
2. Sustained rise below a depth threshold: a YELLOW/ORANGE card displays the
   backend's trend explanation; no frontend depth-only override.
3. Critical depth followed by one low reading: RED is held and recovery is
   visibly being checked. A further qualifying reading shows confirmed recovery.
4. Duplicate polling/retries: the same event is not repeated in the panel.
5. Stale or unknown readings: no normal/current-action claim; historical events
   remain clearly separate from current conditions.
6. Alert-history request failure: show its error while preserving current sensor
   cards; recovering the request restores the timeline without duplicate entries.
7. Page refresh: persisted events reload. Check desktop and narrow-screen layout.

Use mocked status/event responses in the existing Node tests. For manual API
testing, use an isolated demo database and strictly increasing observation times.
`source: simulated` allows a demo observation clock. Backend Phase 4 will add
repeatable scenario controls separately.

## Deliverable

One frontend PR containing the panel, card explanations, relevant Node tests,
and desktop/narrow-screen screenshots. Run:

```powershell
node --test 03_dashboard/tests/dashboard.test.cjs
python -B -m pytest 02_backend/tests 03_dashboard/tests -q
```

Suggested PR title: **Frontend: alert history and Phase 3 explanations**.
