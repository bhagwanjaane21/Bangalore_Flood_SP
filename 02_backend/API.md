# Prototype API contract — Phase 3

Run `python 02_backend/run.py`. Interactive schemas are available at `/docs`.
All URLs below are relative to the backend origin. This prototype has no
authentication; source labels are client declarations, not verified identities.

## Telemetry ingestion

`POST /api/telemetry`, JSON body:

```json
{
  "node_id": "SENSOR_01",
  "water_depth_m": 0.65,
  "battery_voltage": 3.8,
  "observed_at": "2026-11-22T10:30:00+05:30",
  "source": "simulated"
}
```

- Node IDs are `SENSOR_01`, `SENSOR_02`, `SENSOR_03`; unknown IDs receive 400.
- Depth and battery voltage must be finite, nonnegative JSON numbers. Booleans,
  numeric strings and non-finite numbers are rejected with 422.
- Battery and `rate_of_rise_cm_min` are optional and may be null. Missing values
  remain unknown; the server does not assume a battery reading or zero rise rate.
  Reported rise rate can be negative and must be finite.
- `observed_at` is optional. Provided values must include an offset or `Z` and
  are normalized to UTC with microsecond precision. If omitted, the server uses
  receipt time and labels `timestamp_source` as `server_received`.
- `source` is `unknown` (default), `simulated`, `hardware`, or `manual`.
- Non-simulated observation times more than five minutes in the future are
  rejected. Simulated time can be accelerated and is separate from real receipt time.

The response retains `status`, `node_id`, `alert_level`, `lead_time`, and
`recorded_depth_m`. Additive fields include `reading_id`, `observed_at`,
`received_at`, `source`, `timestamp_source`, `rate_source`, reported/effective
rise rates and `lead_time_source`. The lead-time source is explicitly
`unvalidated_assumption`; these fixed windows are not calibrated forecasts.

The second timestamped observation for a node calculates signed rate as
`(depth_delta_m / elapsed_seconds) * 6000`, in cm/min. The original client rate
is retained as `reported_rate_of_rise_cm_min`. Until a previous timestamped
reading exists, `rate_source` is `reported` or `unavailable`.

Retries with the same node, normalized observation time, depth, battery, source
and reported rate return 200 with `status: duplicate` and the original ID/receipt
time. They do not refresh freshness or insert rows. Conflicting data at the same
time, or a new reading older than the node's latest observation, receives 409.
Concurrent retries are serialized in a database transaction.

## Current status and history

`GET /api/sensors/status` preserves the map keyed by sensor ID. It only includes
nodes that have readings. Compare it with the sensor GeoJSON catalog to identify
missing nodes; absence does not mean zero depth or normal conditions.

Each entry retains depth, rise rate, battery, name, alert level, lead-time text
and `last_updated`; it adds reading ID, observation/receipt times, source,
timestamp/rate provenance and freshness:

- `data_status: fresh | stale | unknown`
- `data_age_seconds`: age used for freshness, or null for legacy records.
- `lead_time_source: unvalidated_assumption`.

For `simulated` records, freshness uses real receipt time. Other sources also
consider observation age. Legacy rows with no reliable timezone are `unknown`.
The alert colour is the persisted decision from the common alert engine,
including trend evidence and recovery hysteresis. It is separate from freshness;
a stale colour cannot establish present conditions.

New decision fields on ingestion, status and history include `alert_reason`,
`alert_kind`, `alert_action`, `alert_rule_version`, `recovery_count`,
`trend_rate_cm_min`, `trend_sample_count`, and `quality_flags`. `data_warning`
explains stale/unknown status on ingestion and current-status responses. The
frontend may consume these fields without changing its existing alert colours.

Status reads are side-effect free: they update the displayed age/freshness but
do not recalculate hazard state or create alert events. A duplicate submission
returns that observation's original decision; use the status API for the latest
node decision. Legacy history may have null decision fields where no decision
was recorded; migration does not invent a full historical event log.

`GET /api/sensors/history/{node_id}?limit=120` returns a list of the latest N
persisted readings, oldest first. Allowed limits are 1–1000; bad limits receive
422 and unknown nodes receive 404. A known node with no data returns `[]`.
Each item contains `reading_id`, `node_id`, depths/rates, battery, observation and
receipt times, provenance and a compatibility `timestamp`. Legacy observation
times are null and their original naive timestamp is preserved.

Charts should use stored observation times and IDs, retain gaps for missing
readings, and reload history on refresh. Repeated polling must not append the
same observation with a new browser timestamp.

## Shared prototype alert rules

All thresholds and decisions are owned by `app/alert_engine.py`. Ingestion saves
the decision and status returns that same decision. The previous standalone
rainfall/Telegram demo engine has been replaced; rainfall is not being measured
or inferred by these rules.

| Node | YELLOW depth (m) | ORANGE depth (m) | RED depth (m) |
| --- | --- | --- | --- |
| SENSOR_01 | 0.50 | 1.00 | 1.50 |
| SENSOR_02 | 0.60 | 1.10 | 1.60 |
| SENSOR_03 | 0.80 | 1.20 | 1.50 |

Comparisons are inclusive. These preserve the existing API's depth thresholds.
In particular, SENSOR_02 uses 1.10 m for ORANGE; the earlier root README's 1.00 m
description is not the active rule. None of these levels has field calibration.

- **Critical depth:** a local RED depth escalates immediately, including the
  first observation. Other sensors and rise-rate evidence are not prerequisites.
- **Sustained rise:** use up to five contiguous, same-source observations with
  known times. At least three observations spanning ten seconds are needed by
  default. Median signed rate and both latest intervals must meet 2 cm/min for
  YELLOW or 5 cm/min for ORANGE. This can warn before a depth threshold is crossed.
  These are explicit prototype choices, not validated prediction thresholds.
- **Evidence breaks:** an observation or real-receipt gap exceeding the configured
  freshness interval breaks the trend window. Changing source also breaks it.
  Client-reported rate alone cannot trigger a trend warning. The Phase 2 pairwise
  rate is retained for compatibility; the engine's windowed trend is separate.
- **Recovery:** by default, two distinct fresh observations must be at least
  0.05 m below the current alert's entry threshold before lowering the alert.
  A lower level must also be supported by current trend rules. A bounce, stale
  reading, source break or sampling gap resets earlier recovery evidence.
- **Data quality:** `abrupt_change` flags large changes for inspection without
  discarding a critical depth reading. Short spans and insufficient samples are
  explicit quality flags. They do not establish that a sensor is faulty.
- **Historical data:** a high historical reading can establish a last-observed
  hazard, with stale/unknown evidence labelled. It cannot establish current
  conditions or confirm recovery. Consumers must check freshness before treating
  `alert_action` or an event as a current instruction.

The recovery count, decision and any resulting event are committed atomically
with the observation. Duplicate retries do not advance recovery or create events.
Rule version is `prototype-v1`. Policy changes apply to new observations; past
decisions retain their recorded reasons. Hardware and field validation are future work.

## Alert event history

`GET /api/alerts?limit=50` returns the latest N transitions, oldest first. A first
non-GREEN reading escalates from the initial GREEN state. Initial GREEN readings
and unchanged levels create no events. Each transition contains:

- `event_id`, `node_id`, `reading_id`, `previous_level`, `new_level`;
- `reason`, `event_type` (`escalation` or `recovery`), `rule_version`;
- `observed_at`, real `created_at`, `source`, `data_status_at_event`;
- the observation's `water_depth_m` and `rate_of_rise_cm_min`.

Limits are 1–1000. `node_id=SENSOR_01` optionally filters a node; an unknown node
returns 404. `after_id=0` (or another nonnegative event ID) changes the query to
return the **next** N events after that cursor, ascending, so a polling consumer
does not skip a burst. There is no delivery/acknowledgment mutation in this phase.

Events persist across restart. Migrating a Phase 1/2 database establishes the
latest state without emitting retrospective events. Elapsed time alone changes
freshness, not hazard event history. A future dispatcher must verify current
freshness and track delivery separately; this checkpoint sends no Telegram messages.

## Spatial APIs

- `GET /api/spatial/sensors`: sensor Point FeatureCollection and
  `metadata.verification_status: unverified`.
- `GET /api/spatial/bypass`: mapped LineString FeatureCollection, verification
  status and `metadata.geometry_length_m`, calculated from submitted coordinates.
- `GET /01_spatial_data/manyata_bypass_1.6km.geojson`: compatibility alias for bypass.

Missing/empty spatial datasets return 404. Malformed JSON, incorrect geometry
types, non-finite coordinates and invalid longitude/latitude bounds return 503.
Only these explicit files are served; the API does not expose arbitrary paths.
Geometry length measures the submitted trace, not a surveyed canal alignment.
