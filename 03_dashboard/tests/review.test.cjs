const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../assets/review.js'), 'utf8');

function harness(initial) {
  const items = new Map();
  const timers = new Map();
  let timerId = 0;
  let result = initial;
  function make() {
    return { dataset: {}, attributes: {}, listeners: {}, children: [], hidden: false,
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes; },
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      addEventListener(type, fn) { this.listeners[type] = fn; } };
  }
  function get(id) {
    if (!items.has(id)) items.set(id, make());
    return items.get(id);
  }
  const context = {
    document: { createElement: make, getElementById: get }, AbortController,
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    async fetch() { return { ok: result !== null, json: async () => result }; }
  };
  vm.runInNewContext(script, context);
  return { get, timers, async poll(next) {
    result = next;
    const [id, fn] = timers.entries().next().value;
    timers.delete(id);
    await fn();
  }};
}

function recording(size, status = 'running') {
  return {
    mode: 'simulation', run_id: 'run-1', scenario: 'recovery', total_frames: status === 'completed' ? size : 4,
    status, preview_count: 0, frames: Array.from({ length: size }, (_, i) => ({
      label: 'Step ' + i, offset_seconds: i * 30, previews: [], stations: [{
        data_status: i === 2 ? 'stale' : 'fresh', alert_level: 'RED',
        node_id: 'SENSOR_03', source: 'simulated', water_depth_m: 1.6, name: 'Gate 4',
        observed_at: '2026-11-22T12:30:00Z', alert_reason: 'Critical depth'
      }]
    }))
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('manual selection stays put as new checkpoints arrive, and latest resumes following', async () => {
  const ui = harness(recording(2));
  await settle();
  assert.equal(ui.get('steps').value, '1');
  ui.get('previous').listeners.click();
  await ui.poll(recording(3));
  assert.equal(ui.get('steps').value, '0');
  assert.equal(ui.get('previous').disabled, true);
  ui.get('latest').listeners.click();
  assert.equal(ui.get('recorded-stations').children[0].dataset.level, 'UNKNOWN');
  await ui.poll(recording(4, 'completed'));
  assert.equal(ui.get('steps').value, '3');
  assert.equal(ui.get('next').disabled, true);
  assert.equal(ui.timers.size, 0);
});

test('a completed run opens at the first step and displays original dry-run text literally', async () => {
  const data = recording(2, 'completed');
  data.frames[1].previews.push({ node_id: 'SENSOR_03', previous_level: 'GREEN', new_level: 'RED', text: '<preview>' });
  data.preview_count = 1;
  const ui = harness(data);
  await settle();
  assert.equal(ui.get('steps').value, '0');
  ui.get('steps').listeners.change({ target: { value: '1' } });
  const detail = ui.get('recorded-previews').children[0];
  assert.equal(detail.children[1].textContent, '<preview>');
  assert.equal(ui.get('run-state').textContent, 'Verified');
});

test('a lost connection preserves the selected step and polling recovers', async () => {
  const ui = harness(recording(2));
  await settle();
  ui.get('previous').listeners.click();
  await ui.poll(null);
  assert.match(ui.get('connection-note').textContent, /Retrying/);
  assert.equal(ui.get('steps').value, '0');
  await ui.poll(recording(2, 'failed'));
  assert.equal(ui.get('run-state').textContent, 'Failed');
  assert.equal(ui.get('steps').value, '0');
});

test('a normal server and an older recording provide a restart guide', async () => {
  for (const data of [{ mode: 'monitoring' }, { mode: 'simulation', status: 'not_recorded' }]) {
    const ui = harness({ ...data, frames: [] });
    await settle();
    assert.equal(ui.get('start-guide').hidden, false);
    assert.equal(ui.get('run').hidden, true);
    assert.equal(ui.timers.size, 0);
  }
});

test('selecting a checkpoint compares level and millimetre depth by station ID', async () => {
  const data = recording(2, 'completed');
  data.frames[0].stations[0].water_depth_m = .20;
  data.frames[0].stations[0].alert_level = 'GREEN';
  data.frames[1].stations[0].water_depth_m = .201;
  data.frames[1].stations.unshift({ ...data.frames[1].stations[0], node_id: 'SENSOR_02', name: 'Culvert' });
  const ui = harness(data);
  await settle();
  const buttons = ui.get('checkpoint-list').children.map(item => item.children[0]);
  assert.equal(buttons[0].attributes['aria-current'], 'step');
  buttons[1].listeners.click();
  assert.equal(buttons[1].attributes['aria-current'], 'step');
  assert.equal(buttons[0].attributes['aria-current'], undefined);
  assert.equal(ui.get('steps').value, '1');
  assert.equal(ui.get('frame-position').textContent, 'Step 2 of 2');
  const [middle, downstream] = ui.get('recorded-stations').children;
  assert.equal(downstream.children[0].textContent, 'GREEN → RED');
  assert.equal(downstream.children[2].textContent, '0.201 m');
  assert.equal(downstream.children[3].textContent, '0.001 m higher than the previous step');
  assert.equal(middle.children[3].textContent, 'Starting depth');
});

test('an expanded preview and checkpoint controls retain their nodes during polling', async () => {
  const data = recording(2);
  data.frames[1].previews.push({ node_id: 'SENSOR_03', previous_level: 'GREEN', new_level: 'RED', text: 'Dry run' });
  data.preview_count = 1;
  const ui = harness(data);
  await settle();
  const detail = ui.get('recorded-previews').children[0];
  const choice = ui.get('checkpoint-list').children[1].children[0];
  assert.match(detail.children[0].textContent, /Gate 4/);
  detail.children[0].listeners.click();
  detail.open = true;
  const later = recording(3);
  later.frames[1] = data.frames[1];
  later.preview_count = 1;
  await ui.poll(later);
  assert.equal(ui.get('steps').value, '1');
  assert.equal(ui.get('recorded-previews').children[0], detail);
  assert.equal(ui.get('recorded-previews').children[0].open, true);
  assert.equal(ui.get('checkpoint-list').children[1].children[0], choice);
  assert.equal(ui.get('next').disabled, false);
});

test('a malformed refresh retains the last usable run including working navigation', async () => {
  const ui = harness(recording(2));
  await settle();
  await ui.poll({ ...recording(3), frames: [null] });
  assert.match(ui.get('connection-note').textContent, /still explore/);
  ui.get('previous').listeners.click();
  assert.equal(ui.get('frame-title').textContent, 'Step 0');
  await ui.poll(recording(3));
  assert.equal(ui.get('steps').value, '0');
  assert.equal(ui.get('connection-note').dataset.error, 'false');
});

test('stale, invalid and cross-source measurements never yield a change in water depth', async () => {
  const data = recording(4, 'completed');
  data.frames[1].stations[0].source = 'manual';
  data.frames[3].stations[0].water_depth_m = -1;
  const ui = harness(data);
  await settle();
  ui.get('next').listeners.click();
  assert.match(ui.get('recorded-stations').children[0].children[3].textContent, /No comparable/);
  ui.get('next').listeners.click();
  assert.equal(ui.get('recorded-stations').children[0].dataset.level, 'UNKNOWN');
  assert.equal(ui.get('recorded-stations').children[0].children[3].textContent, 'Last recorded depth');
  ui.get('next').listeners.click();
  assert.equal(ui.get('recorded-stations').children[0].dataset.level, 'UNKNOWN');
  assert.equal(ui.get('recorded-stations').children[0].children[2].textContent, 'Unavailable');
});

test('an incomplete or internally inconsistent finished recording never becomes verified', async () => {
  const ui = harness(recording(2));
  await settle();
  await ui.poll({ ...recording(3, 'completed'), total_frames: 4 });
  assert.equal(ui.get('run-state').textContent, 'Recording');
  await ui.poll({ ...recording(4, 'completed'), preview_count: 1 });
  assert.equal(ui.get('run-state').textContent, 'Recording');
  await ui.poll(recording(4, 'completed'));
  assert.equal(ui.get('run-state').textContent, 'Verified');
});
