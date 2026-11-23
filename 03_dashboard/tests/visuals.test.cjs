const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const script = readFileSync(path.join(root, '03_dashboard/assets/visuals.js'), 'utf8');
const geo = file => JSON.parse(readFileSync(path.join(root, '01_spatial_data', file), 'utf8'));
const catalog = geo('sensors.geojson').features.map(f => ({ ...f.properties, coordinates: f.geometry.coordinates }));
const bypass = geo('manyata_bypass_1.6km.geojson');

function mount(width = 560) {
  class Element {
    constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.handlers = {}; this.textContent = ''; }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(type, fn) { this.handlers[type] = fn; }
    dispatch(type, event) { this.handlers[type]?.(event); }
    focus() { document.activeElement = this; }
    *tree() {
      yield this;
      for (const child of this.children) yield* child.tree();
    }
  }
  const document = { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), activeElement: null };
  // No network, external chart library or map service is provided in this context.
  const context = vm.createContext({ document });
  vm.runInContext(script, context);
  const host = new Element('div');
  host.clientWidth = width;
  const all = predicate => [...host.tree()].filter(predicate);
  const byClass = name => all(node => node.attrs.class === name || node.className === name);
  return { host, document, visuals: context.StreamVisuals, all, byClass };
}

test('local station map fits the real catalog north-up and accounts for latitude in its scale', () => {
  const { host, visuals, all } = mount();
  const map = visuals.createMap(host);
  map.setCatalog(catalog);
  map.setBypass(bypass);
  const markers = all(node => node.attrs['data-station']);
  assert.equal(markers.length, 3);
  const xy = node => node.attrs.transform.match(/translate\(([^ ]+) ([^)]+)\)/).slice(1).map(Number);
  const [x1, y1] = xy(markers[0]), [x2, y2] = xy(markers[1]);
  assert.ok(y1 < y2 && x1 < x2);
  const [lon1, lat1] = catalog[0].coordinates, [lon2, lat2] = catalog[1].coordinates;
  const projectionRatio = ((x2 - x1) / (lon2 - lon1)) / ((y2 - y1) / (lat1 - lat2));
  assert.ok(Math.abs(projectionRatio - Math.cos(13.05 * Math.PI / 180)) < 0.0001);
  assert.ok(all(node => node.textContent === 'Manyata Gate 4').length);
  assert.equal(all(node => node.tag === 'polyline')[0].attrs.points.split(' ').length, bypass.features[0].geometry.coordinates.length);
  const toggle = all(node => node.type === 'checkbox')[0];
  toggle.checked = false;
  toggle.dispatch('change');
  assert.equal(all(node => node.tag === 'polyline').length, 0);
  toggle.checked = true;
  toggle.dispatch('change');
  assert.equal(all(node => node.tag === 'polyline').length, 1);
});

test('the original three conditional routes disappear on stale, missing, invalid or recovered evidence', () => {
  const { host, visuals, byClass } = mount(280);
  const map = visuals.createMap(host);
  map.setCatalog(catalog);
  const gate = (level, freshness) => ({ SENSOR_03: { alert_level: level, data_status: freshness } });
  for (const state of [gate('RED', 'stale'), gate('GREEN', 'fresh'), gate('constructor', 'fresh'), {}]) {
    map.setStatus(gate('RED', 'fresh'));
    assert.equal(map.getRoutesVisible(), true);
    assert.equal(byClass('corridor-vicinity').length, 1);
    assert.equal(byClass('corridor-detour').length, 2);
    assert.equal(byClass('corridor-route-key')[0].hidden, false);
    map.setStatus(state);
    assert.equal(map.getRoutesVisible(), false);
    assert.equal(byClass('corridor-vicinity').length + byClass('corridor-detour').length, 0);
    assert.equal(byClass('corridor-route-key')[0].hidden, true);
  }
  map.setStatus(gate('RED', 'stale'));
  assert.equal(byClass('station-dot')[2].attrs.fill, byClass('station-dot')[0].attrs.fill);
  assert.equal(byClass('station-state')[2].textContent, 'Stale');
  map.setStatus(gate('RED', 'fresh'));
  map.setCatalog(catalog.slice(0, 2));
  assert.equal(map.getRoutesVisible(), false);
});

test('station order arrows retain corridor order when catalog features are reordered and stop at missing stations', () => {
  const { host, visuals, byClass } = mount();
  const map = visuals.createMap(host);
  map.setCatalog([catalog[2], catalog[0], catalog[1]]);
  assert.deepEqual(byClass('corridor-order').map(group => group.children[0].textContent),
    ['Station order · Lake sluice to Mid-reach culvert', 'Station order · Mid-reach culvert to Manyata Gate 4']);
  assert.equal(byClass('corridor-order-key')[0].hidden, false);
  map.setCatalog([catalog[0], catalog[2]]);
  assert.equal(byClass('corridor-order').length, 0);
  assert.equal(byClass('corridor-order-key')[0].hidden, true);
});

test('fitting all Gate 4 routes in a phone-width map leaves separate rows for station labels', () => {
  // At a 320 CSS-pixel viewport the map panel itself is about 242 pixels wide.
  const { host, visuals, all } = mount(242);
  const map = visuals.createMap(host);
  map.setCatalog(catalog);
  map.setBypass(bypass);
  map.setStatus({ SENSOR_03: { alert_level: 'RED', data_status: 'fresh' } });
  const markers = all(node => node.attrs['data-station']);
  const rows = markers.map(node => Number(node.attrs.transform.match(/ ([-.\d]+)\)/)[1]));
  assert.ok(rows[1] - rows[0] >= 30);
  assert.ok(rows[2] - rows[1] >= 30);
  for (const path of all(node => node.tag === 'polyline')) {
    for (const point of path.attrs.points.split(' ')) {
      const [x, y] = point.split(',').map(Number);
      assert.ok(x >= 10 && x <= 232 && y >= 10 && y <= 255);
    }
  }
});

test('map selection works with keyboard, keeps focus while extents change and opens the selected location', () => {
  const { host, document, visuals, all } = mount();
  let selected;
  const map = visuals.createMap(host, id => { selected = id; map.setSelected(id); });
  map.setCatalog(catalog);
  const station = all(node => node.attrs['data-station'] === 'SENSOR_02')[0];
  station.focus();
  let prevented = false;
  station.dispatch('keydown', { key: 'Enter', preventDefault() { prevented = true; } });
  assert.equal(selected, 'SENSOR_02');
  assert.equal(prevented, true);
  assert.equal(station.attrs['aria-pressed'], 'true');
  const street = all(node => node.tag === 'a')[0];
  assert.match(street.href, /13\.05292\/77\.61806$/);
  map.setStatus({ SENSOR_03: { alert_level: 'RED', data_status: 'fresh' } });
  assert.equal(document.activeElement.attrs['data-station'], 'SENSOR_02');
  document.activeElement.dispatch('click');
  assert.equal(selected, '');
});

test('bad geometry cannot create a marker or route and absent spatial data has an explicit empty state', () => {
  const { host, visuals, all } = mount();
  const map = visuals.createMap(host);
  map.setCatalog([{ Sensor_ID: 'SENSOR_03', coordinates: [null, 13] }, { Sensor_ID: 'BAD', coordinates: [77, 210] }]);
  map.setBypass({ features: [{ geometry: { type: 'LineString', coordinates: [[77, 13], [NaN, 13]] } }] });
  map.setStatus({ SENSOR_03: { data_status: 'fresh', alert_level: 'RED' } });
  assert.equal(all(node => node.attrs['data-station'] || node.tag === 'polyline').length, 0);
  assert.equal(all(node => node.type === 'checkbox')[0].disabled, true);
  assert.equal(all(node => node.tag === 'a')[0].hidden, true);
  assert.ok(all(node => /Awaiting station/.test(node.textContent)).length);
});

test('hydrograph draws individual readings, broken intervals, and only the selected station', () => {
  const { host, visuals, all } = mount(300);
  const chart = visuals.createChart(host, catalog);
  const first = Date.parse('2026-11-23T00:00:00Z');
  chart.data.datasets[0].data = [{ x: first, y: 0 }, { x: first + 20000, y: 0.2 }, { x: first + 120000, y: null }, { x: first + 240000, y: 0.4 }];
  chart.data.datasets[1].data = [{ x: first + 240000, y: 0.8 }];
  chart.update();
  let drawn = all(node => node.attrs['data-series'] === 'SENSOR_01');
  assert.deepEqual(drawn.map(node => node.tag), ['polyline', 'circle']);
  assert.equal(drawn[0].attrs.points.split(' ').length, 2);
  chart.setStation('SENSOR_02');
  drawn = all(node => node.attrs['data-series']);
  assert.equal(drawn.length, 1);
  assert.equal(drawn[0].attrs['data-series'], 'SENSOR_02');
  assert.equal(all(node => node.type === 'range')[0].disabled, true);
  chart.setStation('SENSOR_03');
  assert.ok(all(node => node.textContent === 'No timestamped readings to plot').length);
  chart.setStation('');
  assert.equal(all(node => node.attrs['data-series']).length, 3);
});

test('chart inspection prints exact stored observations in Bengaluru time and holds an older selection through refresh', () => {
  const { host, visuals, all, byClass } = mount();
  const chart = visuals.createChart(host, catalog);
  const first = Date.parse('2026-11-22T20:00:00Z');
  chart.data.datasets[0].data = [{ x: first, y: 0.000125 }, { x: first + 20000, y: 0.5 }];
  chart.data.datasets[1].data = [{ x: first + 20000, y: 0.1 }];
  chart.update();
  const slider = all(node => node.type === 'range')[0];
  assert.match(slider.attrs['aria-valuetext'], /23 Nov 2026, 01:30:20 IST/);
  slider.value = '0';
  slider.dispatch('input');
  assert.match(byClass('sample-details')[0].textContent, /Station 01 · 0\.000125 m/);
  assert.doesNotMatch(byClass('sample-details')[0].textContent, /Station 02/);
  chart.data.datasets[0].data.push({ x: first + 40000, y: 0.7 });
  chart.update();
  assert.equal(slider.value, '0');
  assert.match(slider.attrs['aria-valuetext'], /01:30:00/);
  assert.ok(all(node => node.textContent === '01:30:00').length);
  slider.value = slider.max;
  slider.dispatch('input');
  chart.data.datasets[0].data.push({ x: first + 60000, y: 0.6 });
  chart.update();
  assert.equal(slider.value, slider.max);
  assert.match(slider.attrs['aria-valuetext'], /01:31:00/);
});

test('invalid chart points and cleared history never produce a zero-depth observation or a stale curve', () => {
  const { host, visuals, all, byClass } = mount();
  const chart = visuals.createChart(host, catalog);
  chart.data.datasets[0].data = [null, { x: 0, y: null }, { x: NaN, y: 1 }, { x: 1e20, y: 1 }, { x: 23, y: -1 }];
  chart.update();
  assert.equal(byClass('hydro-inspector')[0].hidden, true);
  chart.data.datasets[0].data.push({ x: 1, y: 1.5e308 });
  chart.update();
  assert.equal(all(node => node.attrs['data-series']).length, 1);
  assert.ok(all(node => Object.values(node.attrs).some(value => /NaN|Infinity/.test(value))).length === 0);
  chart.data.datasets[0].data = [];
  chart.update();
  assert.equal(all(node => node.attrs['data-series']).length, 0);
  assert.equal(byClass('hydro-inspector')[0].hidden, true);
});

test('depth reference lines use the selected station rules, stay optional and clear with lost history', () => {
  const { host, visuals, byClass } = mount(300);
  const stations = catalog.map((station, index) => ({ ...station,
    depth_thresholds_m: index === 0 ? { YELLOW: 0.52, ORANGE: 1.04, RED: 1.56 } : null }));
  const chart = visuals.createChart(host, stations);
  chart.data.datasets[0].data = [{ x: 1000, y: .2 }];
  chart.data.datasets[1].data = [{ x: 2000, y: .4 }];
  chart.update();
  const label = byClass('hydro-toggle')[0], toggle = label.children[0];
  assert.equal(label.hidden, true);
  chart.setStation('SENSOR_01');
  assert.equal(label.hidden, false);
  assert.deepEqual(byClass('depth-reference'), []);
  toggle.checked = true;
  toggle.dispatch('change');
  const references = byClass('depth-reference');
  assert.deepEqual(references.map(el => el.attrs['data-threshold']), ['YELLOW', 'ORANGE', 'RED']);
  assert.ok(Number(references[0].attrs.y1) > Number(references[2].attrs.y1));
  assert.equal(byClass('threshold-key-item')[0].textContent, 'Yellow 0.52 m');
  chart.setStation('SENSOR_02');
  assert.equal(label.hidden, true);
  assert.equal(byClass('depth-reference').length, 0);
  chart.setStation('SENSOR_01');
  chart.data.datasets[0].data = [];
  chart.update();
  assert.equal(label.hidden, true);
  assert.equal(byClass('depth-reference').length, 0);
});

test('a click on a time gap selects stored evidence and preserves keyboard inspection', () => {
  const { host, visuals, byClass, all } = mount(500);
  const chart = visuals.createChart(host, catalog);
  chart.data.datasets[0].data = [{ x: 0, y: 0.12 }, { x: 1000, y: .21 }, { x: 9000, y: null }, { x: 10000, y: .52 }];
  chart.data.datasets[1].data = [{ x: 10000, y: .64 }];
  chart.update();
  const plot = byClass('hydro-plot')[0];
  plot.viewBox = { baseVal: { width: 500 } };
  plot.getBoundingClientRect = () => ({ left: 20, width: 250 });
  plot.dispatch('click', { clientX: 105 });
  const slider = all(node => node.type === 'range')[0];
  assert.equal(slider.value, '1');
  assert.match(slider.attrs['aria-valuetext'], /0\.21 m/);
  assert.doesNotMatch(slider.attrs['aria-valuetext'], /Station 02/);
  assert.equal(byClass('hydro-focus-points')[0].children.length, 1);
  slider.value = '2';
  slider.dispatch('input');
  assert.equal(byClass('hydro-focus-points')[0].children.length, 2);
});
