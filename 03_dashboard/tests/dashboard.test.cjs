const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n');
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
    trend_rate_cm_min: null,
    trend_sample_count: 1,
    recovery_count: 0,
    quality_flags: [],
    data_warning: dataStatus === 'fresh' ? null : 'Observation age exceeds freshness threshold',
    ...overrides,
  };
}

async function boot(history = [], config = undefined, alerts = []) {
  const elements = new Map();
  const calls = [];
  const state = { status: {}, history, alerts, failStatus: false, failHistory: false, failAlerts: false };
  class Element {
    constructor() { this.style = {}; this.dataset = {}; this.children = []; this.textContent = ''; }
    set innerHTML(value) { this.html = value; if (!value) this.children = []; }
    get innerHTML() { return this.html || ''; }
    appendChild(child) { this.children.push(child); }
    getContext() { return {}; }
  }
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); }
  };
  function layer() {
    return { layers: new Set(), addTo(parent) { parent.layers.add(this); return this; },
      bindPopup() { return this; }, setPopupContent() {}, setStyle() {} };
  }
  const map = Object.assign(layer(), {
    setView() { return this; }, hasLayer(item) { return this.layers.has(item); }, removeLayer(item) { this.layers.delete(item); }
  });
  const context = vm.createContext({ document, console, AbortController, STREAM_DASHBOARD_CONFIG: config,
    setTimeout() { return 1; }, clearTimeout() {},
    L: { map: () => map, tileLayer: layer, layerGroup: layer, polyline: layer,
      circleMarker: layer, geoJSON: layer, control: { layers: layer } },
    Chart: class { constructor(ctx, config) { this.data = config.data; } update() {} },
    async fetch(url) {
      calls.push(url);
      let value;
      let ok = true;
      if (url === '/api/spatial/sensors') value = { metadata: { stale_after_seconds: 90 }, features: ids.map(id => ({ properties: { Sensor_ID: id }, geometry: { coordinates: [77.6, 13] } })) };
      else if (url === '/api/spatial/bypass') value = { metadata: { geometry_length_m: 992.2 } };
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
  assert.equal(app.read('map.hasLayer(detourLayerGroup)'), true);
  assert.match(app.element('detour-banner').textContent, /SIMULATED/);
  assert.match(app.element('detour-banner').textContent, /passability is unverified/);
  app.state.status.SENSOR_03.data_status = 'stale';
  await app.poll();
  assert.equal(app.read('map.hasLayer(detourLayerGroup)'), false);
  assert.match(app.element('detour-banner').textContent, /current conditions are unknown/);
  app.state.failStatus = true;
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'CONNECTION UNAVAILABLE');
  assert.equal(app.element('detour-banner').style.display, 'none');
  app.state.failStatus = false;
  app.state.status = Object.fromEntries(ids.map(id => [id, reading()]));
  await app.poll();
  assert.equal(app.element('system-badge').textContent, 'ALL SENSORS NORMAL');
  assert.equal(app.read('map.hasLayer(detourLayerGroup)'), false);
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
  assert.match(card1, /abrupt_change/);
  assert.match(card1, /\+3\.40 cm\/min \(4 samples\)/);
  assert.match(card1, /1\/2 consecutive fresh readings/);

  const card2 = app.element('sensor-cards').children[1].innerHTML;
  assert.match(card2, /Stale\/unknown warning: Last reading is 140s old/);
  assert.match(card2, /Paused \(0\/2 fresh below-threshold steps/);
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
  assert.match(panel.children[0].innerHTML, /SENSOR_01: YELLOW → GREEN/);
  assert.match(panel.children[0].innerHTML, /RECOVERY/);
  assert.match(panel.children[1].innerHTML, /SENSOR_01: GREEN → YELLOW/);

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