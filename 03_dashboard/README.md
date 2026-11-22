# STREAM dashboard integration

This dashboard combines Vanshika's PR #1 (bypass layer and hydrograph) and PR #2
(conditional detour illustration). Her original commits are preserved, and the
supporting fixes are maintained separately on `fix/frontend-integration`.
`backend/vernit` contains no frontend implementation changes.

Start the backend with `python 02_backend/run.py` and open its `/` page. Serve the
page through FastAPI so the relative API URLs use the same origin. A standalone
file URL or unrelated static server is not the supported demo setup.

## Integration fixes

- Sensor coordinates come from the sensor catalog instead of duplicated defaults.
- Missing/stale readings are gray and never establish normal conditions. A failed
  status request clears current indications and displays a connection warning.
- The hydrograph reloads persisted history, uses stored observation times and
  does not append readings every browser poll. Legacy times are omitted rather
  than guessed; intervals longer than 90 seconds remain gaps by default. If the
  backend freshness setting is customized, set
  `globalThis.STREAM_DASHBOARD_CONFIG = { historyGapSeconds: 120 }` before the
  dashboard script to match it. No backend API changes are required.
- A fresh Gate 4 RED reading displays the illustrative alternative paths. When
  data becomes stale the paths are removed and the last RED condition is shown
  as uncertain. Recovery removes the overlay. Automatic overlay state cannot
  conflict with a separate manual layer-control checkbox.
- Bypass geometry is retained exactly from PR #1. Its mapped length is about
  0.99 km; the reported 1.6 km is kept separately in the source properties.
  Alignment and road passability remain unverified pending GIS work.
- Network failures are visible; polling starts again only after the previous
  status/history cycle finishes. Optional map/chart-library failures leave sensor
  monitoring available when possible.
- The existing visual layout is retained with a narrow-screen layout adjustment.

Leaflet 1.9.4, Chart.js 4.4.9 and OpenStreetMap tiles are loaded from the internet.
The map/chart are not an offline bundle. Third-party asset reachability and real
browser rendering are separate from the behavior tests below.

## Behavior tests

Requires Node.js with the built-in test runner; no npm installation is needed:

```powershell
node --test 03_dashboard/tests/dashboard.test.cjs
python -B -m pytest 02_backend/tests 03_dashboard/tests -q
```

Tests execute the real dashboard script with DOM/map/chart stubs. They cover no
data, partial/stale data, connection failure/recovery, conditional overlays,
historical timestamps, duplicate points, chart gaps and refresh behavior. They
do not replace a real browser rendering or physical-device LAN check. The Python
integration tests launch a real server with an isolated database and verify the
actual spatial files, source labels, history, duplicate retries and stale data.
