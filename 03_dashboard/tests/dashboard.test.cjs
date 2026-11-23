const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '..', 'assets', 'dashboard.js'), 'utf8');
const ids = ['SENSOR_01', 'SENSOR_02', 'SENSOR_03'];

function reading(level = 'GREEN', dataStatus = 'fresh', overrides = {}) {
  return {
    name: 'Demo sensor',
    water_depth_m: 0.2,
    alert_level: level,
    data_status: dataStatus,
    source: 'simulated',
    rate_of_rise_cm_min: null,
    rate_source: 'unavailable',
    observed_at: '2026-01-01T00:00:00Z',
    lead_time: 'Unvalidated window',
    alert_reason: level === 'GREEN' ? 'Within normal depth and rise thresholds' : `Threshold triggered for ${level}`,
    alert_action: level === 'GREEN' ? 'Continue routine monitoring' : 'Inspect corridor',
    alert_kind: 'depth',
    trend_rate_cm_min: null,
    trend_sample_count: 1,
    recovery_count: 0,
    quality_flags: [],
    data_warning: dataStatus === 'fresh' ? null : 'Observation age exceeds freshness threshold',
    ...overrides,
  };
}

async function boot(history = [], config = undefined, alerts = [], options = {}) {
  const elements = new Map();
  const calls = [];
  const state = { status: {}, history, alerts, ...options };
  class Element {
    constructor() { this.style = {}; this.dataset = {}; this.children = []; this.textContent = ''; }
    set innerHTML(value) { this.html = value; this.children = []; }
    get innerHTML() { return [this.html || '', ...this.children.map(child => child.innerHTML || child.textContent)].join(''); }
    addEventListener(type, listener) { this[type] = listener; }
    appendChild(child) { this.children.push(child); }
    getContext() { return {}; }
  }
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); }
  };
  const context = vm.createContext({ document, console, AbortController, STREAM_DASHBOARD_CONFIG: config,
    setTimeout() { return 1; }, clearTimeout() {},
    StreamVisuals: {
      createMap(element, select) { return { select, setCatalog(catalog) { this.catalog = catalog; },
        setBypass() {}, setStatus(data) { this.status = data; }, setSelected(id) { this.selection = id; },
        getRoutesVisible() { return this.status.SENSOR_03?.alert_level === 'RED' && this.status.SENSOR_03?.data_status === 'fresh'; } }; },
      createChart(element, catalog) { return { data: { datasets: catalog.map(() => ({ data: [] })) },
        update() {}, setStation(id) { this.selection = id; } }; },
    },
    async fetch(url) {
      calls.push(url);
      if (state.customFetch) {
        const override = state.customFetch(url);
        if (override) return override;
      }
      let value;
      let ok = true;
      if (url === '/health') { value = state.health || { status: 'ok' }; ok = !state.failHealth; }
      else if (url === '/api/review') { value = state.replay; ok = !state.failReplay; }
      else if (url === '/api/sensors/catalog') {
        ok = !state.failCatalog;
        value = { expected_sensor_ids: ids, stale_after_seconds: 90, ...state.metadata,
          stations: ids.map(id => ({ Sensor_ID: id, name: id + ' station', depth_thresholds_m: { YELLOW: .5, ORANGE: 1, RED: 1.5 } })) };
      }
      else if (url === '/api/spatial/sensors') {
        ok = !state.failLocations;
        value = { features: (state.mappedIds || ids).map(id => ({
          properties: { Sensor_ID: id },
          geometry: { coordinates: [77.6, 13] }
        })) };
      }
      else if (url === '/api/spatial/bypass') { value = { metadata: { geometry_length_m: 992.2 } }; ok = !state.failBypass; }
      else if (url === '/api/sensors/status') { value = state.status; ok = !state.failStatus; }
      else if (url.includes('/history/')) { value = url.includes('SENSOR_01') ? state.history : []; ok = !state.failHistory; }
      else if (url.startsWith('/api/alerts')) { value = state.alerts; ok = !state.failAlerts; }
      else throw new Error(`Unexpected URL ${url}`);
      return { ok, status: ok ? 200 : 503, async json() { return structuredClone(value); } };
    }
  });
  vm.runInContext(script, context);
  await vm.runInContext('initialLoad', context);
  return {
    state, calls, elements,
    element: id => document.getElementById(id),
    poll: () => vm.runInContext('poll()', context),
    read: expression => vm.runInContext(expression, context),
  };
}

test('empty startup shows three no-data cards and uses only same-origin API URLs', async () => {
  const app = await boot();
  assert.equal(app.element('system-badge').textContent, 'NO SENSOR DATA');
  assert.equal(app.element('sensor-cards').children.length, 3);
  assert.ok(app.element('sensor-cards').children.every(card => card.innerHTML.includes('NO DATA')));
  assert.ok(app.calls.every(url => url.startsWith('/')));
  assert.match(app.element('spatial-status').textContent, /0.99 km/);
  assert.match(app.element('alert-history').children[0].textContent, /No alert transitions recorded yet/);
});

test('missing or stale readings cannot produce an all-normal badge', async () => {
  const app = await boot();
  app.state.status = Object.fromEntries(ids.map(id => [id, reading()]));
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'ALL SENSORS NORMAL');
  delete app.state.status.SENSOR_02;
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'PARTIAL SENSOR DATA');
  app.state.status = { SENSOR_03: reading('GREEN', 'stale') };
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'STALE / UNKNOWN DATA');
});

test('detour overlay responds to fresh RED and is removed on stale/error/recovery', async () => {
  const app = await boot();
  app.state.status = { SENSOR_03: reading('RED') };
  await app.poll();
  assert.equal(app.read('map.getRoutesVisible()'), true);
  assert.match(app.element('detour-banner').textContent, /SIMULATED/);
  assert.match(app.element('detour-banner').textContent, /passability is unverified/);
  app.state.status.SENSOR_03.data_status = 'stale';
  await app.poll();
  assert.equal(app.read('map.getRoutesVisible()'), false);
  assert.match(app.element('detour-banner').textContent, /current conditions are unknown/);
  app.state.failStatus = true;
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'CONNECTION UNAVAILABLE');
  assert.equal(app.element('detour-banner').style.display, 'none');
  app.state.failStatus = false;
  app.state.status = Object.fromEntries(ids.map(id => [id, reading()]));
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'ALL SENSORS NORMAL');
  assert.equal(app.read('map.getRoutesVisible()'), false);
});

test('sensor cards display alert reasons, sustained trends, recovery progress, and stale warnings', async () => {
  const app = await boot();
  app.state.status = {
    SENSOR_01: reading('YELLOW', 'fresh', {
      water_depth_m: 0.65,
      alert_reason: 'Depth 0.65 m >= 0.50 m YELLOW threshold',
      trend_rate_cm_min: 3.4,
      trend_sample_count: 4,
      recovery_count: 1,
      alert_kind: 'held',
      quality_flags: ['abrupt_change'],
    }),
    SENSOR_02: reading('ORANGE', 'stale', {
      water_depth_m: 1.15,
      alert_reason: 'Sustained rise >= 5.0 cm/min',
      data_warning: 'Last reading is 140s old; hazard state may be outdated.',
      recovery_count: 0,
    }),
  };
  await app.poll();

  const card1 = app.element('sensor-cards').children[0].innerHTML;
  assert.match(card1, /Depth 0\.65 m &gt;= 0\.50 m YELLOW threshold/);
  assert.match(card1, /Sudden depth change; check the reading/);
  assert.doesNotMatch(card1, /abrupt_change/);
  assert.match(card1, /\+3\.40 cm\/min \(4 samples\)/);
  assert.match(card1, /Recovery being checked \(1 qualifying observation\)/);

  const card2 = app.element('sensor-cards').children[1].innerHTML;
  assert.match(card2, /Stale\/unknown warning: Last reading is 140s old/);
  assert.match(card2, /Current recovery status unknown/);
});

test('alert history panel renders transitions from GET /api/alerts and handles errors', async () => {
  const alerts = [
    {
      event_id: 1,
      node_id: 'SENSOR_01',
      previous_level: 'GREEN',
      new_level: 'YELLOW',
      event_type: 'escalation',
      reason: 'Depth 0.65 m >= 0.50 m threshold',
      water_depth_m: 0.65,
      observed_at: '2026-01-01T00:01:00Z',
      data_status_at_event: 'fresh',
    },
    {
      event_id: 2,
      node_id: 'SENSOR_01',
      previous_level: 'YELLOW',
      new_level: 'GREEN',
      event_type: 'recovery',
      reason: '2 consecutive fresh readings below 0.45 m',
      water_depth_m: 0.40,
      observed_at: '2026-01-01T00:03:00Z',
      data_status_at_event: 'fresh',
    },
  ];
  const app = await boot([], undefined, alerts);
  const panel = app.element('alert-history');
  assert.equal(panel.children.length, 2);
  // Newest event first
  assert.match(panel.children[0].innerHTML, /SENSOR_01 station/);
  assert.match(panel.children[0].innerHTML, /YELLOW → GREEN/);
  assert.match(panel.children[0].innerHTML, /RECOVERY/);
  assert.match(panel.children[1].innerHTML, /SENSOR_01 station/);
  assert.match(panel.children[1].innerHTML, /GREEN → YELLOW/);

  app.state.failAlerts = true;
  await app.poll();
  assert.match(panel.children[0].textContent, /Alert history unavailable/);
});

test('history uses stored times, ignores duplicates/legacy times, and keeps gaps across refresh', async () => {
  const history = [
    { reading_id: 1, observed_at: null, water_depth_m: 0.2 },
    { reading_id: 2, observed_at: '2026-01-01T00:00:00Z', water_depth_m: 0.2 },
    { reading_id: 2, observed_at: '2026-01-01T00:00:00Z', water_depth_m: 0.2 },
    { reading_id: 3, observed_at: '2026-01-01T00:01:00Z', water_depth_m: 0.3 },
    { reading_id: 4, observed_at: '2026-01-01T00:05:00Z', water_depth_m: 0.5 },
  ];
  const app = await boot(history);
  const original = JSON.stringify(app.read('hydroChart.data.datasets[0].data'));
  assert.equal(app.read('hydroChart.data.datasets[0].data.length'), 4);
  assert.equal(app.read('hydroChart.data.datasets[0].data.filter(point => point.y === null).length'), 1);
  await app.poll();
  assert.equal(JSON.stringify(app.read('hydroChart.data.datasets[0].data')), original);
  const refreshed = await boot(history);
  assert.equal(JSON.stringify(refreshed.read('hydroChart.data.datasets[0].data')), original);
  app.state.failHistory = true;
  await app.poll();
  assert.equal(app.read('hydroChart.data.datasets[0].data.length'), 0);
  assert.match(app.element('history-status').textContent, /unavailable/);
});

test('history gap configuration works without requiring backend API changes', async () => {
  const history = [
    { reading_id: 1, observed_at: '2026-01-01T00:00:00Z', water_depth_m: 0.2 },
    { reading_id: 2, observed_at: '2026-01-01T00:01:10Z', water_depth_m: 0.3 },
  ];
  const configured = await boot(history, { historyGapSeconds: 60 });
  assert.equal(configured.read('hydroChart.data.datasets[0].data.length'), 3);
  const invalid = await boot(history, { historyGapSeconds: -1 });
  assert.equal(invalid.read('hydroChart.data.datasets[0].data.length'), 2);
});

for (const dataStatus of ['stale', 'unknown']) {
  for (const level of ['RED', 'GREEN']) {
    test(`${dataStatus} ${level} cards suppress actions and current recovery claims`, async () => {
      const app = await boot();
      app.state.status = { SENSOR_01: reading(level, dataStatus, {
        alert_kind: 'recovery', alert_action: 'CONTACT THE OPERATOR NOW',
        data_warning: 'Current conditions cannot be established.',
      }) };
      await app.poll();
      const card = app.element('sensor-cards').children[0].innerHTML;
      assert.match(card, /Current conditions cannot be established/);
      assert.doesNotMatch(card, /Action:|CONTACT THE OPERATOR NOW/);
      assert.doesNotMatch(card, /Recovery confirmed|Normal \(no active alert\)|Recovered \(/);
      assert.match(card, /Current recovery status unknown/);
    });
  }
}

test('fresh actions remain visible and are hidden again when data becomes unknown', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('RED', 'fresh', { alert_action: 'Contact operator' }) };
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Action: Contact operator/);
  app.state.status.SENSOR_01.data_status = 'unknown';
  await app.poll();
  assert.doesNotMatch(app.element('sensor-cards').children[0].innerHTML, /Action:/);
});

test('held recovery preserves a configurable backend requirement without inventing a target', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('RED', 'fresh', {
    water_depth_m: 1.35, alert_kind: 'held', recovery_count: 2,
    alert_reason: 'Holding RED: recovery observation 2/3 below 1.400 m.',
  }) };
  await app.poll();
  const card = app.element('sensor-cards').children[0].innerHTML;
  assert.match(card, /Holding RED: recovery observation 2\/3 below 1\.400 m\./);
  assert.match(card, /Recovery being checked/);
  assert.match(card, /2 qualifying observations/);
  assert.doesNotMatch(card, /2\/2|0\.05 m|Recovery confirmed/);
  assert.equal(app.read("document.getElementById('sensor-cards').children[0].className"), 'card RED');
  // The backend can hold an alert with no qualifying observations yet.
  app.state.status.SENSOR_01.recovery_count = 0;
  app.state.status.SENSOR_01.alert_reason = 'Holding RED: recovery requires depth <= 1.400 m.';
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Recovery being checked/);
  assert.doesNotMatch(app.element('sensor-cards').children[0].innerHTML, /0\/2|0\.05 m/);
});

for (const level of ['GREEN', 'ORANGE']) {
  test(`confirmed recovery to ${level} uses alert_kind even when its counter resets`, async () => {
    const app = await boot();
    app.state.status = { SENSOR_01: reading(level, 'fresh', {
      alert_kind: 'recovery', recovery_count: 0,
      alert_reason: `Recovery RED -> ${level} confirmed by 3 fresh observations below 1.400 m.`,
    }) };
    await app.poll();
    const card = app.element('sensor-cards').children[0].innerHTML;
    assert.match(card, /Recovery confirmed/);
    assert.doesNotMatch(card, /0\/2|Normal \(no active alert\)/);
    assert.equal(app.read("document.getElementById('sensor-cards').children[0].className"), `card ${level}`);
  });
}

test('legacy readings with no decision kind do not invent recovery evidence', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('RED', 'fresh', { alert_kind: null, recovery_count: null }) };
  await app.poll();
  const card = app.element('sensor-cards').children[0].innerHTML;
  assert.match(card, /Recovery status unavailable/);
  assert.doesNotMatch(card, /0\/2|0\.05 m|Recovery confirmed/);
});

test('historical source and evidence freshness remain separate from current stale conditions', async () => {
  const events = [{ event_id: 42, node_id: 'SENSOR_01', previous_level: 'GREEN', new_level: 'RED',
    event_type: 'escalation', reason: 'Historical depth crossing', source: 'simulated',
    observed_at: '2026-01-01T00:01:00Z', data_status_at_event: 'fresh' }];
  const app = await boot([], undefined, events);
  app.state.status = { SENSOR_01: reading('RED', 'stale') };
  await app.poll();
  const panel = app.element('alert-history');
  const event = panel.children[0];
  assert.match(event.innerHTML, /Source: simulated \(prototype\)/);
  assert.match(event.innerHTML, /Evidence when recorded: fresh/);
  assert.equal(event.dataset.eventId, '42');
  assert.match(html, /id="alert-history-note"[^>]*>[^<]*evidence available at that time/);
  assert.match(app.element('sensor-cards').children[0].innerHTML, /STALE/);
  await app.poll();
  assert.equal(panel.children.length, 1);
  const refreshed = await boot([], undefined, events);
  assert.equal(refreshed.element('alert-history').children[0].dataset.eventId, '42');
});

test('history error and recovery preserve fresh sensor cards and restore one copy per event', async () => {
  const events = [{ event_id: 8, node_id: 'SENSOR_02', previous_level: 'YELLOW', new_level: 'GREEN',
    event_type: 'recovery', reason: 'Confirmed recovery', source: 'manual', data_status_at_event: 'fresh' }];
  const app = await boot([], undefined, events);
  app.state.status = Object.fromEntries(ids.map(id => [id, reading()]));
  app.state.failAlerts = true;
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'ALL SENSORS NORMAL');
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Action:/);
  assert.match(app.element('alert-history').children[0].textContent, /Alert history unavailable/);
  app.state.failAlerts = false;
  await app.poll();
  await app.poll();
  assert.equal(app.element('alert-history').children.length, 1);
  assert.match(app.element('alert-history').children[0].innerHTML, /Source: manual/);
});

test('history fields are escaped and absent provenance stays unknown', async () => {
  const app = await boot([], undefined, [{ event_id: 9, node_id: '<img onerror=bad()>',
    previous_level: 'GREEN', new_level: 'RED', reason: '<script>bad()</script>',
    source: '<svg onload=bad()>', data_status_at_event: '<b>fresh</b>', event_type: 'escalation' }]);
  let card = app.element('alert-history').children[0].innerHTML;
  assert.doesNotMatch(card, /<img|<script|<svg|<b>/);
  assert.match(card, /Source: &lt;svg onload=bad\(\)&gt;/);
  assert.match(card, /Evidence when recorded: &lt;b&gt;fresh&lt;\/b&gt;/);
  app.state.alerts = [{ event_id: 10, node_id: 'SENSOR_01', previous_level: 'GREEN', new_level: 'YELLOW' }];
  await app.poll();
  card = app.element('alert-history').children[0].innerHTML;
  assert.match(card, /Source: unknown/);
  assert.match(card, /Evidence when recorded: unknown/);
});

test('data age uses the backend age, including simulated clocks, and updates on polling', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('GREEN', 'fresh', {
    observed_at: '2099-01-01T00:00:00Z', data_age_seconds: 12.4,
  }) };
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Data age: 12 s/);
  app.state.status.SENSOR_01.data_age_seconds = 125;
  app.state.status.SENSOR_01.data_status = 'stale';
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Data age: 2 min 5 s/);
  assert.doesNotMatch(app.element('sensor-cards').children[0].innerHTML, /Action:/);
});

test('missing, invalid and unavailable ages stay unknown rather than becoming zero', async () => {
  const app = await boot();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Data age: Unknown/);
  for (const value of [null, undefined, -1, NaN, Infinity, '12']) {
    app.state.status = { SENSOR_01: reading('GREEN', 'unknown', { data_age_seconds: value }) };
    await app.poll();
    assert.match(app.element('sensor-cards').children[0].innerHTML, /Data age: Unknown/);
  }
  app.state.status.SENSOR_01.data_age_seconds = 0;
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /Data age: Less than 1 s/);
  assert.equal(app.read('formatDataAge(3661)'), '1 h 1 min');
});

test('quality notes explain limited evidence without changing the stored hazard decision', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('RED', 'fresh', { quality_flags: [
    'abrupt_change', 'abrupt_change', 'insufficient_trend_samples', 'sampling_gap',
  ] }) };
  await app.poll();
  const card = app.element('sensor-cards').children[0].innerHTML;
  assert.match(card, /Sudden depth change; check the reading/);
  assert.match(card, /does not confirm a sensor fault/);
  assert.match(card, /More observations are needed/);
  assert.match(card, /gap between readings/);
  assert.doesNotMatch(card, /abrupt_change|insufficient_trend_samples|sampling_gap/);
  assert.equal((card.match(/Sudden depth change/g) || []).length, 1);
  assert.equal(app.element('sensor-cards').children[0].className, 'card RED');
});

test('future or unsafe quality flags receive a generic note without rendering raw content', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('GREEN', 'fresh', {
    quality_flags: ['<img onerror=bad()>', '__proto__', 'unrecognized_future_flag'],
  }) };
  await app.poll();
  const card = app.element('sensor-cards').children[0].innerHTML;
  assert.match(card, /Additional observation quality information is unavailable/);
  assert.doesNotMatch(card, /<img|__proto__|unrecognized_future_flag/);
  app.state.status.SENSOR_01.quality_flags = null;
  await app.poll();
  assert.doesNotMatch(app.element('sensor-cards').children[0].innerHTML, /Observation quality notes/);
});

test('fresh backend normal decisions are recognized without inventing recovery evidence', async () => {
  const app = await boot();
  app.state.status = { SENSOR_01: reading('GREEN', 'fresh', { alert_kind: 'normal' }) };
  await app.poll();
  assert.match(app.element('sensor-cards').children[0].innerHTML, /No active alert/);
  assert.doesNotMatch(app.element('sensor-cards').children[0].innerHTML, /Recovery confirmed|Recovery status unavailable/);
});

test('dashboard loads local assets and exposes a review replay and station filter', () => {
  const assets = [...html.matchAll(/<(?:script|link)\b[^>]*(?:src|href)="([^"]+)"/g)].map(match => match[1]);
  assert.ok(assets.length >= 4);
  for (const href of assets) {
    assert.ok(href.startsWith('/assets/'));
    assert.ok(fs.existsSync(path.join(__dirname, '..', href.slice(1))));
  }
  assert.match(html, /<a[^>]+href="\/review"/);
  assert.match(html, /<label[^>]+for="station-filter"/);
});

test('corridor counts use only current station readings and clear on a failed status check', async () => {
  const app = await boot();
  app.state.status = {
    SENSOR_01: reading('RED', 'stale', { water_depth_m: 9 }),
    SENSOR_02: reading('GREEN', 'fresh', { water_depth_m: 0, source: 'manual' }),
    SENSOR_03: reading('YELLOW', 'fresh', { water_depth_m: .83 }),
    UNREGISTERED: reading('RED', 'fresh', { water_depth_m: 30 }),
  };
  await app.poll();
  assert.equal(app.element('metric-coverage').textContent, '2 / 3');
  assert.equal(app.element('metric-alerts').textContent, '1');
  assert.equal(app.element('metric-depth').textContent, '0.83 m');
  assert.equal(app.element('depth-station').textContent, 'SENSOR_03 station');
  assert.equal(app.element('metric-source').textContent, 'Mixed');
  assert.equal(app.element('system-badge').dataset.level, 'YELLOW');
  app.state.failStatus = true;
  await app.poll();
  for (const metric of ['coverage', 'alerts', 'depth', 'source']) assert.equal(app.element('metric-' + metric).textContent, '—');
  assert.ok(app.element('sensor-cards').children.every(card => card.innerHTML.includes('Latest reading unavailable')));
  app.state.failStatus = false;
  app.state.status = { SENSOR_02: reading('GREEN', 'fresh', { water_depth_m: 0 }) };
  await app.poll();
  assert.equal(app.element('metric-depth').textContent, '0.00 m');
  assert.equal(app.element('metric-alerts').textContent, '0');
  assert.equal(app.element('metric-source').textContent, 'Simulated');
});

test('malformed fresh data never produces an all-normal report or recommended action', async () => {
  const app = await boot();
  for (const bad of [{ alert_level: 'constructor' }, { water_depth_m: -1 }, { water_depth_m: '1.8' }, { data_status: null }]) {
    app.state.status = Object.fromEntries(ids.map(id => [id, reading('RED', 'fresh', bad)]));
    await app.poll();
    assert.equal(app.element('metric-coverage').textContent, '0 / 3');
    assert.equal(app.element('system-badge').textContent, 'STALE / UNKNOWN DATA');
    assert.ok(app.element('sensor-cards').children.every(card => card.className === 'card UNKNOWN' && !card.innerHTML.includes('Action:')));
    assert.equal(app.read('map.getRoutesVisible()'), false);
  }
});

test('catalog and bypass recover automatically and stationary detail nodes stay open across polls', async () => {
  const app = await boot([], undefined, [], { failCatalog: true, failBypass: true, metadata: { expected_sensor_ids: ids }, mappedIds: [ids[0], ids[2]] });
  assert.equal(app.element('system-badge').textContent, 'SENSOR CATALOG UNAVAILABLE');
  app.state.failCatalog = false;
  app.state.status = Object.fromEntries(ids.map(id => [id, reading()]));
  await app.poll();
  assert.match(app.element('spatial-status').textContent, /mapping unavailable/i);
  assert.equal(app.element('metric-coverage').textContent, '3 / 3');
  assert.equal(app.read('map.catalog.length'), 2);
  const first = app.element('sensor-cards').children[0];
  assert.match(first.innerHTML, /Depth thresholds: yellow 0.50 m/);
  const details = first.children[1];
  details.open = true;
  app.state.failBypass = false;
  await app.poll();
  assert.match(app.element('spatial-status').textContent, /0.99 km/);
  assert.equal(app.element('sensor-cards').children[0].children[1], details);
  assert.equal(details.open, true);
  assert.equal(app.element('station-filter').children.length, 3);
});

test('station filter uses its own server history, focuses visuals, and keeps corridor totals', async () => {
  const alerts = ids.map((id, index) => ({ node_id: id, event_id: index + 1, previous_level: 'GREEN', new_level: 'YELLOW' }));
  const app = await boot([], undefined, alerts, { status: Object.fromEntries(ids.map(id => [id, reading('YELLOW')])) });
  await app.read("map.select('SENSOR_02')");
  assert.equal(app.element('station-filter').value, 'SENSOR_02');
  assert.equal(app.read('map.selection'), 'SENSOR_02');
  assert.equal(app.read('hydroChart.selection'), 'SENSOR_02');
  assert.deepEqual(app.element('sensor-cards').children.map(card => card.hidden), [true, false, true]);
  assert.ok(app.calls.includes('/api/alerts?limit=20&node_id=SENSOR_02'));
  assert.equal(app.element('alert-history').children.length, 1);
  assert.equal(app.element('alert-history').children[0].dataset.eventId, '2');
  assert.equal(app.element('metric-alerts').textContent, '3');
  await app.element('station-filter').change({ target: { value: '' } });
  assert.ok(app.element('sensor-cards').children.every(card => !card.hidden));
  assert.equal(app.element('alert-history').children.length, 3);
});

test('complete GIS failure leaves telemetry, charts and alert history available and retries mapping', async () => {
  const app = await boot([{ reading_id: 1, observed_at: '2026-01-01T00:00:00Z', water_depth_m: .2 }], undefined,
    [{ event_id: 3, node_id: 'SENSOR_03', previous_level: 'GREEN', new_level: 'RED' }], {
      failLocations: true, failBypass: true, status: { SENSOR_03: reading('RED') },
    });
  assert.equal(app.element('system-badge').textContent, 'RED ALERT · COVERAGE INCOMPLETE');
  assert.match(app.element('spatial-status').textContent, /locations unavailable/);
  assert.equal(app.element('alert-history').children[0].dataset.eventId, '3');
  assert.match(app.element('history-status').textContent, /1 dated reading/);
  app.state.failLocations = false;
  app.state.failBypass = false;
  await app.poll();
  assert.equal(app.read('map.catalog.length'), 3);
  assert.doesNotMatch(app.element('spatial-status').textContent, /unavailable/);
});

test('an older station request cannot overwrite a newer selection', async () => {
  const app = await boot();
  let finish;
  app.state.customFetch = url => url.endsWith('=SENSOR_02')
    && new Promise(resolve => { finish = resolve; });
  const pending = app.read("selectStation('SENSOR_02')");
  await app.read("selectStation('SENSOR_03')");
  finish({ ok: true, async json() { return [{ node_id: 'SENSOR_02', event_id: 20 }]; } });
  await pending;
  assert.equal(app.element('station-filter').value, 'SENSOR_03');
  assert.equal(app.element('alert-history').children[0].dataset.eventId, undefined);
  assert.match(app.element('alert-history').children[0].textContent, /No alert transitions/);
});

test('hydrographs respect server gap, source boundaries, and interrupted intervals', async () => {
  const rows = [0, 30, 31, 32, 33, 34, 33, 35].map((seconds, index) => ({
    reading_id: index + 1, observed_at: new Date(Date.UTC(2026, 0, 1, 0, 0, seconds)).toISOString(),
    source: index < 2 ? 'simulated' : 'manual', water_depth_m: index === 4 ? -1 : .3,
  }));
  const app = await boot(rows, undefined, [], { metadata: { stale_after_seconds: 20 } });
  const points = app.read('hydroChart.data.datasets[0].data');
  assert.equal(points.filter(point => point.y === null).length, 4);
  assert.equal(points.filter(point => point.y !== null).length, 6);
  assert.match(app.read("timeLabel('2026-01-01T00:00:00Z')"), /05:30:00 IST$/);
  assert.equal(app.read("timeLabel('bad')"), 'Unknown');
});

test('timezone-less and missing-provenance readings cannot bridge a plotted interval', async () => {
  const row = (reading_id, observed_at, source) => ({ reading_id, observed_at, source, water_depth_m: .3 });
  const app = await boot([
    row(1, '2026-01-01T00:00:00Z', 'hardware'),
    row(2, '2026-01-01T00:00:01', 'hardware'),
    row(3, '2026-01-01T00:00:02Z', 'hardware'),
    row(4, '2026-01-01T00:00:03Z'),
    row(5, '2026-01-01T00:00:04Z', 'manual')
  ]);
  const points = app.read('hydroChart.data.datasets[0].data');
  assert.equal(points.filter(point => point.y === null).length, 3);
  assert.equal(points.filter(point => point.y !== null).length, 4);
  assert.equal(app.read("timeLabel('2026-01-01T00:00:01')"), 'Unknown');
});

test('recorded-run invitation tracks the active server and recovers after a lost connection', async () => {
  const app = await boot([], undefined, [], {
    health: { status: 'ok', simulation_run_id: 'run-a' },
    replay: { run_id: 'run-a', status: 'completed', scenario: 'progressing-surge', frames: [{}, {}] }
  });
  const invite = app.element('replay-invitation');
  assert.equal(invite.hidden, false);
  assert.match(app.element('replay-title').textContent, /Completed simulation · progressing surge/);
  assert.match(app.element('replay-evidence').textContent, /2 recorded checkpoints/);
  await app.read('refreshReplayInvitation()');
  assert.equal(app.calls.filter(url => url === '/api/review').length, 1);
  app.state.health.simulation_run_id = 'run-b';
  await app.read('refreshReplayInvitation()');
  assert.equal(invite.hidden, true);
  app.state.replay = { run_id: 'run-b', status: 'running', scenario: 'recovery', frames: [{}] };
  await app.read('refreshReplayInvitation()');
  assert.equal(invite.hidden, false);
  assert.match(app.element('replay-title').textContent, /Simulation in progress · recovery/);
  app.state.failHealth = true;
  await app.read('refreshReplayInvitation()');
  assert.equal(invite.hidden, true);
  app.state.failHealth = false;
  await app.read('refreshReplayInvitation()');
  assert.equal(invite.hidden, false);
  delete app.state.health.simulation_run_id;
  await app.read('refreshReplayInvitation()');
  assert.equal(invite.hidden, true);
});

test('historical sources remain labelled and historical depths never display a current depth ruler', async () => {
  const app = await boot([], undefined, [], { status: { SENSOR_03: reading('RED', 'fresh', {water_depth_m: 2}) } });
  const card = app.element('sensor-cards').children[2];
  assert.match(card.innerHTML, /--measured:100\.00%/);
  assert.match(card.innerHTML, /Critical depth 1\.50 m/);
  assert.match(app.element('assessment-note').textContent, /Highest warning at SENSOR_03 station/);
  app.state.status.SENSOR_03.data_status = 'stale';
  await app.poll();
  assert.doesNotMatch(card.innerHTML, /depth-scale/);
  assert.equal(app.element('metric-depth').textContent, '—');
  assert.equal(app.element('metric-source').textContent, 'Simulated');
  assert.equal(app.element('source-caption').textContent, 'Last recorded observations');
  assert.match(app.element('assessment-note').textContent, /Fresh readings are needed/);
});

test('unchanged transitions keep their DOM rows and a changed payload at the same ID refreshes', async () => {
  const alert = {event_id: 1, node_id: 'SENSOR_01', previous_level: 'GREEN', new_level: 'YELLOW'};
  const app = await boot([], undefined, [alert]);
  const panel = app.element('alert-history');
  const before = panel.children[0];
  await app.poll();
  assert.equal(panel.children[0], before);
  alert.new_level = 'ORANGE';
  await app.poll();
  assert.notEqual(panel.children[0], before);
  assert.match(panel.children[0].innerHTML, /GREEN → ORANGE/);
});
