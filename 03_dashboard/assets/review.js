'use strict';

const element = id => document.getElementById(id);
const names = {
  'progressing-surge': 'Progressing surge', 'sustained-rise': 'Sustained rise', normal: 'Stable water',
  recovery: 'Recovery', 'isolated-spike': 'Isolated spike', 'sensor-dropout': 'Station dropout'
};
const states = {waiting: 'Preparing', running: 'Recording', completed: 'Verified', failed: 'Failed', cancelled: 'Interrupted'};
const levels = new Set(['GREEN', 'YELLOW', 'ORANGE', 'RED']);
const observationTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
});
let run, index = 0, follow = true;
const stepButtons = [];

function node(tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function validDepth(value) {
  return Number.isFinite(value) && value >= 0;
}

function metres(value) {
  if (value > 0 && value < .001) return '<0.001 m';
  // Keep millimetre changes visible in the sustained-rise and isolated-spike scenarios.
  return value.toFixed(3).replace(/(\.\d{2})0$/, '$1') + ' m';
}

function levelAt(station) {
  return station?.data_status === 'fresh' && validDepth(station.water_depth_m) && levels.has(station.alert_level)
    ? station.alert_level : 'UNKNOWN';
}

function depthChange(current, last) {
  if (levelAt(current) === 'UNKNOWN') return 'Last recorded depth';
  if (!last) return 'Starting depth';
  if (levelAt(last) === 'UNKNOWN' || !['simulated', 'manual', 'hardware'].includes(current.source)
      || current.source !== last.source) return 'No comparable prior reading';
  const difference = current.water_depth_m - last.water_depth_m;
  if (difference === 0) return 'Unchanged since the previous step';
  const distance = Math.abs(difference);
  // Subtraction of decimal sensor values can yield .0009999999999999 for one millimetre.
  const accurate = Number(distance.toPrecision(12));
  return metres(accurate) + (difference > 0 ? ' higher' : ' lower') + ' than the previous step';
}

function stationCard(station, last) {
  const card = document.createElement('article');
  card.className = 'recorded-station';
  const level = levelAt(station);
  card.dataset.level = level;
  const previous = levelAt(last);
  let badge = level;
  if (previous !== 'UNKNOWN' && level !== 'UNKNOWN' && previous !== level) badge = previous + ' → ' + level;
  if (level === 'UNKNOWN' && station.data_status === 'stale') badge = 'UNKNOWN · stale';
  const pill = node('span', badge, 'state-pill');
  const depth = validDepth(station.water_depth_m) ? metres(station.water_depth_m) : 'Unavailable';
  card.append(pill, node('h4', station.name || station.node_id || 'Station'), node('strong', depth));
  card.append(node('p', depthChange(station, last), 'depth-change'));
  const reason = level === 'UNKNOWN' ? 'A current alert could not be established at this checkpoint.' : station.alert_reason || 'Reason unavailable.';
  card.append(node('p', reason, 'reason'));
  const timestamp = typeof station.observed_at === 'string' && /(?:Z|[+-]\d\d:\d\d)$/i.test(station.observed_at)
    ? Date.parse(station.observed_at) : NaN;
  const observed = Number.isFinite(timestamp) ? observationTime.format(timestamp) + ' IST' : 'time unavailable';
  card.append(node('p', 'Observed ' + observed, 'recorded-time'));
  return card;
}

function clockLabel(seconds) {
  const minutes = Math.floor(seconds / 60);
  return '+' + minutes + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');
}

function describeFrame(frame) {
  const unknown = frame.stations.filter(station => levelAt(station) === 'UNKNOWN').length;
  const changes = frame.previews.length;
  if (unknown === frame.stations.length) return 'Fresh station evidence was unavailable at this step.';
  const sentence = changes ? changes + ' alert ' + (changes === 1 ? 'change' : 'changes') + ' recorded.'
    : (index === 0 ? 'Starting conditions at the monitored stations.' : 'Alert levels held at the reporting stations.');
  return unknown ? sentence + ' ' + unknown + (unknown === 1 ? ' station had' : ' stations had') + ' no verified level at this step.' : sentence;
}

function syncControls() {
  const active = document.activeElement;
  element('steps').value = String(index);
  element('previous').disabled = index === 0;
  element('next').disabled = index === run.frames.length - 1;
  element('latest').disabled = index === run.frames.length - 1 && (follow || run.status !== 'running');
  stepButtons.forEach((button, i) => {
    if (i === index) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  if (active?.disabled && [element('previous'), element('next'), element('latest')].includes(active)) {
    element('frame-title').focus();
  }
}

function showFrame() {
  const frame = run.frames[index];
  const prior = new Map((run.frames[index - 1]?.stations || []).map(station => [station.node_id, station]));
  element('frame-position').textContent = 'Step ' + (index + 1) + ' of ' + run.total_frames;
  element('frame-clock').textContent = 'Scenario time ' + clockLabel(frame.offset_seconds);
  element('frame-title').textContent = frame.label;
  element('frame-summary').textContent = describeFrame(frame);
  element('recorded-stations').replaceChildren(...frame.stations.map(station => stationCard(station, prior.get(station.node_id))));
  const previews = frame.previews.map(preview => {
    const detail = document.createElement('details');
    const station = frame.stations.find(item => item.node_id === preview.node_id);
    const summary = node('summary', (station?.name || preview.node_id) + ' · ' + preview.previous_level + ' → ' + preview.new_level);
    summary.addEventListener('click', () => {
      follow = false;
      syncControls();
    });
    detail.append(summary, node('pre', preview.text));
    return detail;
  });
  element('recorded-previews').replaceChildren(...(previews.length ? previews : [node('p', 'No alert level changed at this checkpoint.')]));
}

function selectStep(selected, followLatest = false) {
  if (!Number.isInteger(selected) || !run?.frames[selected]) return;
  const changed = selected !== index;
  index = selected;
  follow = followLatest;
  syncControls();
  if (changed) showFrame();
}

element('steps').addEventListener('change', e => selectStep(Number(e.target.value)));
element('previous').addEventListener('click', () => selectStep(index - 1));
element('next').addEventListener('click', () => selectStep(index + 1));
element('latest').addEventListener('click', () => selectStep(run.frames.length - 1, true));

function appendChoices(frames, start) {
  for (let i = start; i < frames.length; i++) {
    const frame = frames[i];
    const option = node('option', (i + 1) + '. ' + frame.label);
    option.value = String(i);
    element('steps').append(option);
    const choice = node('button', '', 'checkpoint-choice');
    choice.type = 'button';
    choice.append(node('span', String(i + 1), 'step-number'), node('span', frame.label, 'step-label'),
                  node('span', clockLabel(frame.offset_seconds), 'step-time'));
    choice.addEventListener('click', () => selectStep(i));
    const item = document.createElement('li');
    item.append(choice);
    element('checkpoint-list').append(item);
    stepButtons.push(choice);
  }
}

function checkDocument(data) {
  if (!Array.isArray(data?.frames) || !['monitoring', 'simulation'].includes(data.mode)) throw new Error('Invalid replay');
  if (!data.scenario) {
    if (!data.frames.length && (data.mode === 'monitoring' || ['waiting', 'not_recorded'].includes(data.status))) return;
    throw new Error('Missing scenario');
  }
  const valid = data.mode === 'simulation' && typeof data.run_id === 'string' && Object.hasOwn(states, data.status)
    && Number.isInteger(data.total_frames) && data.total_frames >= data.frames.length && data.total_frames > 0
    && data.frames.every(frame => typeof frame?.label === 'string' && Number.isFinite(frame.offset_seconds) && frame.offset_seconds >= 0
      && Array.isArray(frame.stations) && frame.stations.every(station => station && typeof station === 'object')
      && Array.isArray(frame.previews) && frame.previews.every(preview => preview && typeof preview.text === 'string'));
  if (!valid || !Number.isInteger(data.preview_count) || data.preview_count !== data.frames.reduce((total, frame) => total + frame.previews.length, 0)
      || (data.status === 'completed' && data.frames.length !== data.total_frames)) throw new Error('Incomplete replay');
}

function update(data) {
  checkDocument(data);
  const old = run;
  run = data;
  const note = element('connection-note');
  note.dataset.error = 'false';
  const monitored = data.mode === 'monitoring';
  element('start-guide').hidden = !monitored && data.status !== 'not_recorded';
  element('run').hidden = monitored || !data.scenario;
  if (monitored) {
    note.textContent = 'This server is monitoring incoming readings.';
    return;
  }
  if (!data.scenario) {
    note.textContent = data.status === 'not_recorded'
      ? 'This saved run has a JSON report and no recorded replay. Start a new rehearsal to record one.'
      : 'Waiting for the simulator to record its first step…';
    return;
  }
  note.textContent = ['failed', 'cancelled'].includes(data.status)
    ? 'Playback stopped before verification finished. You can inspect the steps already recorded.' : '';
  element('scenario-name').textContent = Object.hasOwn(names, data.scenario) ? names[data.scenario] : data.scenario;
  element('scenario-description').textContent = data.description;
  element('run-state').textContent = states[data.status];
  element('run-state').dataset.status = data.status;
  element('frame-count').textContent = data.frames.length + ' / ' + data.total_frames;
  element('preview-count').textContent = data.preview_count;
  element('player').hidden = data.frames.length === 0;
  const newSequence = !old || old.run_id !== data.run_id || !old.frames.length || old.frames.length > data.frames.length;
  if (newSequence) {
    follow = data.status === 'running';
    index = 0;
    stepButtons.length = 0;
    element('steps').replaceChildren();
    element('checkpoint-list').replaceChildren();
  }
  if (!data.frames.length) return;
  appendChoices(data.frames, newSequence ? 0 : old.frames.length);
  const priorIndex = index;
  if (follow) index = data.frames.length - 1;
  syncControls();
  // Frames in a run are immutable. Leave an inspected preview open and focused when later frames arrive.
  if (newSequence || priorIndex !== index) showFrame();
}

async function poll() {
  let retry = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/api/review', {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Review unavailable');
    const data = await response.json();
    update(data);
    retry = data.mode === 'simulation' && ['waiting', 'running'].includes(data.status);
  } catch {
    const note = element('connection-note');
    note.dataset.error = 'true';
    note.textContent = run?.frames.length
      ? 'Replay update unavailable. You can still explore the recorded steps. Retrying in two seconds.'
      : 'Could not load the replay. Retrying in two seconds.';
  } finally {
    clearTimeout(timeout);
    if (retry) setTimeout(poll, 2000);
  }
}
poll();
