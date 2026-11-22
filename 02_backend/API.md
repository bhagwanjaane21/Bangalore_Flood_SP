# Prototype API contract — Phase 2

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
The alert colour is the last observed depth classification, separate from
freshness; a stale colour cannot establish present conditions.

`GET /api/sensors/history/{node_id}?limit=120` returns a list of the latest N
persisted readings, oldest first. Allowed limits are 1–1000; bad limits receive
422 and unknown nodes receive 404. A known node with no data returns `[]`.
Each item contains `reading_id`, `node_id`, depths/rates, battery, observation and
receipt times, provenance and a compatibility `timestamp`. Legacy observation
times are null and their original naive timestamp is preserved.

Charts should use stored observation times and IDs, retain gaps for missing
readings, and reload history on refresh. Repeated polling must not append the
same observation with a new browser timestamp.

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
