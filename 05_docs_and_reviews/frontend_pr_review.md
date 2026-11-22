# Frontend PR review and backend integration findings

**Current integration status:** Original teammate PRs
[#1](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/1) and
[#2](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/2) are now merged
into `main` with original commits and PR authorship preserved. Supporting fixes
were merged separately through PR #4; the completed frontend-fix branch and the
temporary prototype backup were retired during Phase 3 cleanup. The initial review
below describes the earlier baseline and remains as historical evidence.

**Branch scope:** This document is a review for backend compatibility. The
current `backend/vernit` work contains no additional frontend implementation
changes relative to main. The merged frontend is inherited by syncing from main.
The earlier prototype backup was retired after the original PRs and supporting
fixes merged successfully. Their commits and PR authorship remain in main.

Reviewed on 22 November 2026. This is a local review; no GitHub review, message,
merge, or remote branch modification was performed.

Main: `c7a2e6f50767bb4a5b3c9f1769fc15806162f3d1`.

## Relevant contributions

| PR | Author / head | Contribution | Assessment |
| --- | --- | --- | --- |
| [#1](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/1) | `vanshika-exe`; `2dceecc5ddcc0c7162a9e41fef48dba3e79c3a26` | Adds a bypass LineString, Leaflet layer controls, and Chart.js water-depth curves. | Relevant to the approved GIS/telemetry prototype. Keep and fix integration/data issues. |
| [#2](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/2) | `vanshika-exe`; `2f7492f442764d65ddd42e8de9271cdaa29ee5ea` | Repeats the chart/layer frontend changes and adds illustrative detour polylines and a SENSOR_03 RED banner. | Fits the roadmap as a visual demonstration. It does not calculate or verify safe routes. Reconcile with PR #1 before merging. |

GitHub reported both individually mergeable against the current main branch.
PR #1 is not an ancestor of PR #2. A local three-way merge-tree check combining
their heads reported a content conflict in `03_dashboard/index.html`.
No working-tree merge was performed.

## Findings requiring action

### 1. Bypass layer cannot load under the existing FastAPI server

Both PRs fetch `../01_spatial_data/manyata_bypass_1.6km.geojson` (PR #1 line 62;
PR #2 line 71). When the dashboard is served at `/`, this resolves to
`/01_spatial_data/manyata_bypass_1.6km.geojson`. The backend has no corresponding
route or static mount. A TestClient probe confirmed HTTP 404.

**Backend:** provide a narrow compatibility route and `/api/spatial/bypass`.
**Frontend:** prefer the API route, handle failures visibly, and check `res.ok`.

### 2. Phone/LAN access still points telemetry at the wrong machine

Both dashboards retain `fetch("http://127.0.0.1:8000/api/sensors/status")`
(PR #1 line 132; PR #2 line 187). This refers to the viewer's own device.
It will not reach a laptop server when opened on a teammate's phone.

**Frontend:** use `/api/sensors/status` when served by FastAPI, or a configurable
backend origin when using a separate frontend server.
**Backend:** document host binding and configure CORS for the actual frontend.

### 3. Missing sensors are plotted as zero, and stale values appear current

PR #1 lines 163–167 and PR #2 lines 235–239 append browser poll time and the
latest value, using zero if a sensor is absent. This fabricates dry readings for
missing data and repeats an old observation with new chart timestamps.

**Backend:** provide observation IDs/times, freshness and persisted history.
**Frontend:** show gaps for missing/stale data, deduplicate observations, display
their real times and reload history after refresh. Do not infer normal status
from an empty response. Similar normal/stale display problems predate these PRs.

### 4. PR #2 alone does not supply the bypass geometry

PR #2 changes only the dashboard. Its bypass GeoJSON remains zero bytes, as on
main, despite the dashboard fetching that file. PR #1 supplies the geometry.
Merging only PR #2 therefore leaves that layer incomplete even after URL serving
is repaired.

**Integration:** retain PR #1's data and use a reconciled dashboard with PR #2's
additional demonstration overlay. Do not overwrite PR #1's file with the empty
file from PR #2's head.

### 5. Bypass length and data provenance need GIS verification

The seven-point LineString in PR #1 has a summed great-circle segment length of
approximately **992 metres**, while its property and popup say **1.6 km**.
This measures the submitted geometry, not the actual canal. A simplified or
partial trace may explain the difference. No survey/source metadata is included.

**GIS team:** reconcile the trace with the QGIS export, record its source/date,
and either correct the geometry or label it as partial/illustrative. Backend work
should not manufacture the missing alignment.

### 6. Detour graphics are not evidence of route safety

PR #2 uses fixed polylines, no road-network routing, no observations on the
alternative roads, and no flood-depth checks along them. SENSOR_03 RED toggles
the entire layer (lines 218 onward). The UI currently calls those paths safe.
The layer control also lets viewers enable the layer independently of the alert.

**Frontend:** label the lines as illustrative alternatives with unverified
passability; distinguish a demonstrated alert from confirmed flooding. If the
layer is intended to be automatic, synchronize manual layer-control state with
the automatic toggle. Keep these graphics out of claims of validated routing.

## Recommended integration order

1. Frontend owner fixes the shared URL, missing-data and chart behaviour in PR #1.
2. GIS owner verifies the bypass export/provenance.
3. Integrate PR #1 once its required backend routes are ready.
4. Rebase/update PR #2 onto the resulting main, resolve overlap once, and retain
   only the additional detour demonstration changes. Ensure geometry is preserved.
5. Test the combined frontend and backend on a fresh database and a second device.

This is a recommendation for the team; no remote merge or teammate contact has
been performed.

## Backend baseline probes

FastAPI TestClient used an isolated temporary SQLite DB. Notification calls were
mocked, and JavaScript syntax checks used Node without a browser.

| Probe | Observed result |
| --- | --- |
| Import `app.main` with `02_backend` on the Python path | Fails with `ModuleNotFoundError: No module named 'database'`; current import assumes the app directory is on the path. |
| Import using the legacy app-directory path | Succeeds; allows endpoint probes. |
| `GET /` and `GET /api/spatial/sensors` | 200. |
| Initial `GET /api/sensors/status` | Empty object, with no expected-node/no-data summary. |
| POST SENSOR_01 depths 0.20, 0.85, 1.25, 1.65 m | 200; GREEN, YELLOW, ORANGE, RED respectively. |
| POST depth -2.0 m | Incorrectly accepted with 200. |
| POST unknown node | Rejected with 400. |
| Bypass file URL; sensor history; `/api/predict`; `/health` | All 404; features are absent, not regressions caused by these PRs. |
| Change stored timestamps to 2020 and read status | Original hazard classification is returned without a stale-data indicator. |
| Run one mocked RED dispatcher iteration | Zero calls to the mocked send function; `datetime` is not imported and the broad exception handler suppresses the error. No external message sent. |
| PR #1 and #2 inline JavaScript syntax | Both pass `node --check`; this does not establish rendering or end-to-end functionality. |
| Combine PR heads with `git merge-tree --write-tree` | Dashboard content conflict; checked without modifying the working tree. |

Additional source findings: database path depends on working directory; rise rate
is accepted from clients but unused by API alert decisions; a separate alert engine
contains different logic; no generated-DB ignore rule exists and a DB is tracked;
the continuous simulator's phase offsets correspond to demo seconds, not the
claimed 20-minute physical lag. The README's lead-time arithmetic is inconsistent:
3,350 m / 0.6 m/s is approximately 93 minutes, not 35–45 minutes. None of these
values establishes actual corridor flood-wave travel time.

No source fix, full browser rendering check, trained-model evaluation or field
validation was performed in this planning checkpoint.

## Frontend integration verification — 22 November 2026

- Original PR #1 merged as `b22ad79`; PR #2 merged as `972040b` after maintainer
  conflict resolution `68651df`. The original teammate heads `2dceecc` and
  `2f7492f` remain ancestors of main. Neither contribution was reauthored.
- Fixes are isolated on `fix/frontend-integration`; `backend/vernit` stays at
  `7a06787`. No backend implementation/configuration changes are part of this fix.
- The UI uses same-origin APIs and actual persisted observation times, avoids
  fabricated zero readings and duplicate chart points, distinguishes no data,
  partial data and stale data, and clears illustrative paths when data is stale.
- The mapped bypass coordinates are unchanged from PR #1. Its calculated length
  is shown as approximately 0.99 km; the reported 1.6 km is preserved separately.
  GIS alignment and road passability are not claimed as verified.
- Validation commands: `python -B -m pytest 02_backend/tests 03_dashboard/tests -q
  -p no:cacheprovider` and `node --test 03_dashboard/tests/dashboard.test.cjs`.
  These cover 62 existing backend tests, two real-server frontend API tests, and
  dashboard behavior tests for no-data/stale states, errors/recovery, overlays,
  historical refresh, chart gaps and configurable gap thresholds.
- Browser rendering and access from a physical second device remain unverified.
