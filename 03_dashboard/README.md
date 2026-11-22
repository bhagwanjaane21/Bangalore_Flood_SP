# STREAM dashboard integration

This dashboard combines Vanshika's PR #1 (bypass layer and hydrograph) and PR #2
(conditional detour illustration). Her original commits are preserved, and the
supporting fixes are maintained separately on `fix/frontend-integration`.
`backend/vernit` contains no frontend implementation changes.

Original contributions by **vanshika-exe** were merged through their own PRs:

- [PR #1](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/1): bypass
  trace, layer controls and live hydrograph; merge commit `b22ad79`.
- [PR #2](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/2): conditional
  detour illustration; merge commit `972040b`. A maintainer merge reconciled the
  overlap while retaining her original commit `2f7492f`.

The separate frontend-integration PR contains API wiring, data-state fixes,
tests and documentation on top of those merged contributions.

Vanshika's [PR #8](https://github.com/bhagwanjaane21/Bangalore_Flood_SP/pull/8)
adds the alert-history panel and sensor decision details (original commit
`9caa0d1`). The follow-up fixes stay on `fix/frontend-integration`; her original
PR is merged first with its authorship preserved. This work changes dashboard
files only and does not update `backend/vernit`.

## Alert timeline and decision explanations

- The timeline reloads the latest 20 persisted transitions, newest first, with
  source labels and stable event IDs. Simulated events say `simulated (prototype)`.
- `Evidence when recorded` describes historical freshness. It never establishes
  current conditions; the panel states this explicitly. Missing provenance is
  shown as unknown. A timeline request failure leaves current sensor cards intact.
- Suggested actions appear only on fresh cards. Stale/unknown cards retain the
  backend's last explanation and show a data warning, without a current action or
  recovery confirmation.
- `alert_kind: held` means recovery is being checked; `recovery` means confirmed,
  even when `recovery_count` resets to zero or the resulting level remains ORANGE.
  Configured targets and release depths come from the backend reason. The UI
  does not assume two readings or a 0.05 m margin. Missing decision kinds stay
  unavailable rather than creating recovery evidence.

Twelve regression cases were added for the three PR review findings. They fail
on the original PR and pass with the follow-up fixes. They cover RED/GREEN with
stale/unknown data, configurable held recovery, confirmed/partial recovery,
legacy decision metadata, historical provenance, polling/refresh/error recovery
and safe escaping. Browser checks and physical-device LAN access are tracked
separately from these automated behavior checks.

## Demo-readiness checkpoint — 22 November 2026

- Cards show `data_age_seconds` from the backend, including in accelerated
  simulations. Missing/invalid ages say Unknown; the browser does not infer age
  from the simulated observation clock.
- Observation quality flags have readable notes. A sudden change requests a
  check without declaring a sensor fault or overriding the stored alert level.
  Unrecognized flags receive a generic note instead of exposing internal names.
- The sidebar scrolls at desktop widths; its content does not shrink to fit.
  Card titles and event headers wrap on narrow screens. The timeline can receive
  keyboard focus for scrolling.
- Actual browser checks at **360, 390, 768, 800, 801 and 1366 px** found no page
  horizontal overflow or overflowing cards/chart/event boxes. The map, external
  chart library and map tiles loaded during this check. Availability still
  depends on an internet connection.
- Browser/API observations covered empty startup, sustained rise, held recovery,
  confirmed recovery, connection loss and stale data. Stale cards showed no
  current actions; the three stored historical events remained available.
- **210 Python tests and 24 dashboard tests passed**. The five additional
  dashboard cases cover age/provenance, readable quality evidence, unsafe/future
  flags and the backend's `normal` decision kind.

Screenshots: [desktop empty](review/desktop-empty.jpg),
[desktop alert](review/desktop-held-recovery.jpg),
[desktop timeline](review/desktop-recovery-timeline.jpg),
[connection error](review/desktop-connection-error.jpg),
[mobile alert](review/mobile-held-recovery.jpg),
[mobile stale](review/mobile-stale.jpg).
Measured layout results are in [layout-checks.json](review/layout-checks.json)
and [browser-checks.json](review/browser-checks.json).

These are browser viewport checks, not a physical phone/LAN test. No live
Telegram delivery, road passability or field calibration is established.

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
