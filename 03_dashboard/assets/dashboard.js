'use strict';

const ranks = { GREEN: 0, YELLOW: 1, ORANGE: 2, RED: 3 };
const el = id => document.getElementById(id);
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char]);
const ist = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
});
const configuredGap = Number(globalThis.STREAM_DASHBOARD_CONFIG?.historyGapSeconds);
let gapLimitMs = 90000;
let catalog = [];
let map = null;
let hydroChart = null;
let catalogReady = false;
let mapReady = false;
let bypassReady = false;
let locationNote = 'Loading station locations…';
let bypassNote = 'Loading mapped bypass…';
let selectedStation = '';
let alertRequest = 0;
let renderedHistory = null;
let reviewRunId = null;
let reviewComplete = false;
const cards = new Map();
const historyAvailability = new Map();

function observationTime(value) {
  // A legacy timestamp without an offset has no known local time zone.
  if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return NaN;
  return Date.parse(value);
}

function timeLabel(value) {
  const time = typeof value === 'number' ? new Date(value).getTime() : observationTime(value);
  return Number.isFinite(time) ? ist.format(time) + ' IST' : 'Unknown';
}

async function getJson(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function buildHistoryPoints(rows) {
  const seen = new Set();
  const points = [];
  let previous = null;
  let previousSource = null;
  let interrupted = false;
  for (const row of rows) {
    const x = observationTime(row?.observed_at);
    if (row && seen.has(row.reading_id)) continue;
    if (!Number.isFinite(x) || !Number.isFinite(row.water_depth_m) || row.water_depth_m < 0 || (previous !== null && x <= previous)) {
      interrupted = true;
      continue;
    }
    seen.add(row.reading_id);
    const source = row.source || 'unknown';
    const changedSource = previousSource !== null && previousSource !== source;
    if (previous !== null && (interrupted || changedSource || x - previous > gapLimitMs)) points.push({ x: (previous + x) / 2, y: null });
    points.push({ x, y: row.water_depth_m });
    previous = x;
    previousSource = source;
    interrupted = false;
  }
  return points;
}

function formatSustainedTrend(reading) {
  if (!reading) return 'Unavailable';
  const samples = Number.isFinite(reading.trend_sample_count) ? reading.trend_sample_count : 0;
  if (Number.isFinite(reading.trend_rate_cm_min)) {
    const sign = reading.trend_rate_cm_min > 0 ? '+' : '';
    return `${sign}${reading.trend_rate_cm_min.toFixed(2)} cm/min (${samples} sample${samples === 1 ? '' : 's'})`;
  }
  return samples > 0 ? `Insufficient window (${samples} sample${samples === 1 ? '' : 's'})` : 'Insufficient samples';
}

function formatDataAge(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Unknown';
  if (seconds < 1) return 'Less than 1 s';
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.floor(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

function qualityNotes(flags) {
  if (!Array.isArray(flags)) return [];
  const labels = {
    abrupt_change: 'Sudden depth change; check the reading. This alone does not confirm a sensor fault.',
    insufficient_trend_samples: 'More observations are needed to establish a sustained trend.',
    trend_span_too_short: 'Observations cover too little time to establish a sustained trend.',
    trend_unavailable: 'A reliable trend could not be calculated.',
    timestamp_unknown: 'Observation time is uncertain.',
    source_changed: 'The observation source changed; the trend needs a new continuous sequence.',
    sampling_gap: 'A gap between readings interrupts the trend evidence.',
    stale_trend_evidence: 'A reading arrived too late to support the trend. More fresh observations are needed.',
    reported_rate_unverified: 'The submitted rise rate has not been verified from stored observations.',
    data_stale: 'The reading is old; current conditions cannot be established.',
    data_unknown: 'Reading freshness is unknown; current conditions cannot be established.',
  };
  return [...new Set(flags.map(flag => Object.prototype.hasOwnProperty.call(labels, flag)
    ? labels[flag] : 'Additional observation quality information is unavailable.'))];
}

function formatRecoveryProgress(reading) {
  if (!reading) return 'Awaiting data';
  if (reading.data_status !== 'fresh') {
    return 'Current recovery status unknown; last decision is historical.';
  }
  // The backend reason contains its configured target and release depth.
  // A confirmed recovery resets recovery_count, and may still leave an alert active.
  if (reading.alert_kind === 'recovery') return 'Recovery confirmed';
  if (reading.alert_kind === 'held') {
    const count = reading.recovery_count;
    const progress = Number.isInteger(count) && count > 0
      ? ` (${count} qualifying observation${count === 1 ? '' : 's'})` : '';
    return `Recovery being checked${progress}`;
  }
  if (['normal', 'depth', 'trend'].includes(reading.alert_kind)) {
    return reading.alert_level === 'GREEN' ? 'No active alert' : 'No recovery confirmation recorded';
  }
  return 'Recovery status unavailable';
}

function setBadge(text, level = 'UNKNOWN') {
  const badge = el('system-badge');
  if (badge.textContent !== text) badge.textContent = text;
  badge.dataset.level = level;
}

function stationName(id) {
  return catalog.find(station => station.Sensor_ID === id)?.name || id || 'Unknown station';
}

// The depth scale shows only a measurement. The backend's decision may also
// depend on sustained rise or recovery and remains the source of the alert level.
function depthScale(depth, bands) {
  if (!bands || ![bands.YELLOW, bands.ORANGE, bands.RED].every(Number.isFinite) ||
      !(0 < bands.YELLOW && bands.YELLOW < bands.ORANGE && bands.ORANGE < bands.RED)) return '';
  const percentage = value => (Math.min(1, value / bands.RED) * 100).toFixed(2) + '%';
  return `<div class="depth-scale" aria-label="Critical depth ${bands.RED.toFixed(2)} metres">
    <div class="depth-track" aria-hidden="true" style="--measured:${percentage(depth)}"><b></b>
      <i style="--threshold:${percentage(bands.YELLOW)}"></i><i style="--threshold:${percentage(bands.ORANGE)}"></i>
    </div><small><span>0 m</span><span>Critical depth ${bands.RED.toFixed(2)} m</span></small></div>`;
}

function createCard(id) {
  const card = document.createElement('article');
  card.dataset.sensorId = id;
  const main = document.createElement('div');
  main.className = 'card-reading';
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = 'Station details';
  const body = document.createElement('div');
  body.className = 'reading-details-body';
  details.appendChild(summary);
  details.appendChild(body);
  card.appendChild(main);
  card.appendChild(details);
  el('sensor-cards').appendChild(card);
  return { card, main, body };
}

function renderCard(sensor, reading, connected) {
  const id = sensor.Sensor_ID;
  if (!cards.has(id)) cards.set(id, createCard(id));
  const { card, main, body } = cards.get(id);
  const fresh = reading?.data_status === 'fresh';
  const level = fresh ? reading.alert_level : 'UNKNOWN';
  const depth = Number.isFinite(reading?.water_depth_m) && reading.water_depth_m >= 0 ? reading.water_depth_m.toFixed(2) + ' m' : '—';
  const state = fresh ? level : reading ? reading.data_status.toUpperCase() : connected ? 'NO DATA' : 'UNAVAILABLE';
  const name = sensor.name || reading?.name || 'Proposed station';
  card.className = 'card ' + level;
  card.hidden = Boolean(selectedStation && selectedStation !== id);
  const last = !fresh && reading ? 'Last recorded depth' : 'Water depth';
  const age = formatDataAge(reading?.data_age_seconds);
  const reason = reading ? escapeHtml(reading.alert_reason || 'No recorded reason (legacy observation)') : '';
  const staleWarning = reading && !fresh
    ? `<div class="stale-warning">Stale/unknown warning: ${escapeHtml(reading.data_warning || 'Current conditions are unknown.')}</div>` : '';
  const action = fresh && reading.alert_action
    ? `<p class="status-note action-note">Action: ${escapeHtml(reading.alert_action)}</p>` : '';
  main.innerHTML = `
    <div class="station-meta"><span class="station-id">${escapeHtml(id)}</span><span class="state-pill">${escapeHtml(state)}</span></div>
    <h3>${escapeHtml(name)}</h3>
    <div class="depth-stat">${depth}</div>
    <p class="observation-line">${last} · Data age: ${escapeHtml(age)}</p>
    ${fresh ? depthScale(reading.water_depth_m, sensor.depth_thresholds_m) : ''}
    ${staleWarning}
    ${reading ? `<p class="reason-text">${fresh ? 'Reason' : 'Last decision'}: ${reason}</p>` : connected ? '<p class="reason-text">Awaiting first observation.</p>' : '<p class="reason-text">Latest reading unavailable.</p>'}
    ${action}`;
  const notes = qualityNotes(reading?.quality_flags);
  const rate = Number.isFinite(reading?.rate_of_rise_cm_min)
    ? `${reading.rate_of_rise_cm_min.toFixed(2)} cm/min (${escapeHtml(reading.rate_source || 'unknown')})` : 'Unknown';
  const thresholds = sensor.depth_thresholds_m;
  const bands = thresholds && ['YELLOW', 'ORANGE', 'RED'].every(key => Number.isFinite(thresholds[key]))
    ? `<p>Depth thresholds: yellow ${thresholds.YELLOW.toFixed(2)} m · orange ${thresholds.ORANGE.toFixed(2)} m · red ${thresholds.RED.toFixed(2)} m.</p>` : '';
  body.innerHTML = `
    ${bands}
    ${reading && !fresh ? `<p>Last level: ${escapeHtml(reading.alert_level || 'Unknown')}</p>` : ''}
    <p>Observed: ${escapeHtml(timeLabel(reading?.observed_at))}</p>
    <p>Source: ${escapeHtml(reading?.source || 'unknown')}</p>
    <p>Rise / fall: ${rate}</p>
    <p class="trend-text">Sustained trend: ${escapeHtml(formatSustainedTrend(reading))}</p>
    <p class="recovery-text">Recovery progress: ${escapeHtml(formatRecoveryProgress(reading))}</p>
    ${notes.length ? `<ul class="quality-notes" aria-label="Observation quality notes">${notes.map(note => '<li>' + escapeHtml(note) + '</li>').join('')}</ul>` : ''}
    ${reading?.lead_time ? `<p class="lead-time">Assumed travel window: ${escapeHtml(reading.lead_time)} (unvalidated).</p>` : ''}`;
}

function normalizeReading(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const valid = Object.hasOwn(ranks, value.alert_level) &&
    Number.isFinite(value.water_depth_m) && value.water_depth_m >= 0;
  if (value.data_status === 'stale' || (valid && value.data_status === 'fresh')) return value;
  return { ...value, data_status: 'unknown' };
}

function renderStatus(data, connected = true) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid station status');
  const status = Object.create(null);
  for (const sensor of catalog) {
    const id = sensor.Sensor_ID;
    const reading = normalizeReading(data[id]);
    if (reading) status[id] = reading;
    renderCard(sensor, reading, connected);
  }
  map?.setStatus(status);
  const fresh = Object.entries(status).filter(([, reading]) => reading.data_status === 'fresh');
  const alerts = fresh.filter(([, reading]) => ranks[reading.alert_level] > 0);
  const peak = fresh.reduce((best, entry) => best && best[1].water_depth_m >= entry[1].water_depth_m ? best : entry, null);
  const highest = fresh.reduce((level, [, reading]) => ranks[reading.alert_level] > ranks[level] ? reading.alert_level : level, 'GREEN');
  const sourceReadings = fresh.length ? fresh.map(([, reading]) => reading) : Object.values(status);
  const sources = [...new Set(sourceReadings.map(reading => reading.source || 'unknown'))];
  const sourceLabels = { simulated: 'Simulated', manual: 'Manual', hardware: 'Hardware', unknown: 'Unknown', legacy: 'Legacy' };
  el('metric-coverage').textContent = connected ? `${fresh.length} / ${catalog.length}` : '—';
  el('coverage-caption').textContent = connected ? 'With a current reading' : 'Latest check unavailable';
  el('metric-alerts').textContent = fresh.length ? String(alerts.length) : '—';
  el('metric-depth').textContent = peak ? peak[1].water_depth_m.toFixed(2) + ' m' : '—';
  el('depth-station').textContent = peak ? stationName(peak[0]) : 'No fresh depth available';
  el('metric-source').textContent = sources.length > 1 ? 'Mixed' : sources.length ? (Object.hasOwn(sourceLabels, sources[0]) ? sourceLabels[sources[0]] : 'Unknown') : '—';
  el('source-caption').textContent = !fresh.length && sourceReadings.length ? 'Last recorded observations'
    : sources.length === 1 && sources[0] === 'simulated' ? 'Demo observations' : 'Across fresh stations';
  const missing = catalog.length - fresh.length;
  const unavailable = missing === 1 ? '1 station has no current reading.' : `${missing} stations have no current reading.`;
  if (!connected) el('assessment-note').textContent = 'The live status request failed. Current levels are unavailable.';
  else if (!Object.keys(status).length) el('assessment-note').textContent = 'Waiting for the first observation from a station.';
  else if (!fresh.length) el('assessment-note').textContent = 'Fresh readings are needed to assess current water levels.';
  else if (alerts.length) {
    const topSites = alerts.filter(([, reading]) => reading.alert_level === highest).map(([id]) => stationName(id));
    const summary = topSites.length === catalog.length && topSites.length > 1
      ? `All ${topSites.length} stations are at ${highest}.`
      : `Highest warning at ${topSites.join(', ')}.`;
    el('assessment-note').textContent = summary + (missing ? ' ' + unavailable : '');
  } else {
    el('assessment-note').textContent = 'Reporting stations are within their local warning rules.' + (missing ? ' ' + unavailable : '');
  }
  if (!connected) {
    setBadge('CONNECTION UNAVAILABLE');
    el('last-update').textContent = 'Telemetry unavailable · retrying';
  } else {
    el('last-update').textContent = 'Checked ' + timeLabel(Date.now());
    if (alerts.length) setBadge(highest + ' ALERT' + (fresh.length < catalog.length ? ' · COVERAGE INCOMPLETE' : ''), highest);
    else if (!fresh.length) setBadge(Object.keys(status).length ? 'STALE / UNKNOWN DATA' : 'NO SENSOR DATA');
    else if (fresh.length < catalog.length) setBadge('PARTIAL SENSOR DATA');
    else setBadge('ALL SENSORS NORMAL', 'GREEN');
  }
  const gate = status.SENSOR_03;
  const freshRed = gate?.data_status === 'fresh' && gate.alert_level === 'RED';
  const staleRed = gate && !freshRed && gate.alert_level === 'RED';
  const banner = el('detour-banner');
  banner.style.display = freshRed || staleRed ? 'block' : 'none';
  const message = freshRed
    ? `${gate.source === 'simulated' ? 'SIMULATED' : 'PROTOTYPE'} RED alert at Gate 4. Alternative paths are illustrative; road passability is unverified.`
    : staleRed ? 'Last Gate 4 reading was RED; current conditions are unknown. Road passability is unverified.' : '';
  if (banner.textContent !== message) banner.textContent = message;
}

function alertNote(message) {
  renderedHistory = null;
  const panel = el('alert-history');
  const note = document.createElement('p');
  note.className = 'status-note';
  note.textContent = message;
  panel.innerHTML = '';
  panel.appendChild(note);
}

function renderAlertHistory(events) {
  if (!Array.isArray(events)) throw new Error('Invalid alert history');
  const panel = el('alert-history');
  const list = events.filter(event => event && (!selectedStation || event.node_id === selectedStation));
  el('alert-log-count').textContent = `${list.length} shown · latest 20`;
  const snapshot = JSON.stringify(list);
  if (snapshot === renderedHistory) return;
  if (!list.length) {
    alertNote(selectedStation ? 'No alert transitions recorded yet for this station.' : 'No alert transitions recorded yet.');
    renderedHistory = snapshot;
    return;
  }
  panel.innerHTML = '';
  for (const event of [...list].reverse()) {
    const item = document.createElement('div');
    item.className = 'alert-event ' + (Object.hasOwn(ranks, event.new_level) ? event.new_level : 'UNKNOWN');
    item.dataset.eventId = String(event.event_id);
    const source = event.source === 'simulated' ? 'simulated (prototype)' : (event.source || 'unknown');
    item.innerHTML = `
      <div class="event-header">
        <span class="event-site">${escapeHtml(stationName(event.node_id))}</span>
        <span class="event-levels">${escapeHtml(event.previous_level || 'Unknown')} → ${escapeHtml(event.new_level || 'Unknown')}</span>
        <span class="event-kind">${escapeHtml(String(event.event_type || 'transition').toUpperCase())}</span>
      </div>
      <div class="reason-text">${escapeHtml(event.reason || 'State transition')}</div>
      <div class="status-note">Depth: ${Number.isFinite(event.water_depth_m) ? event.water_depth_m.toFixed(2) + ' m' : '—'} · Observed: ${escapeHtml(timeLabel(event.observed_at))}</div>
      <div class="status-note">Source: ${escapeHtml(source)} · Evidence when recorded: ${escapeHtml(event.data_status_at_event || 'unknown')}</div>`;
    panel.appendChild(item);
  }
  // Keep the rows a reviewer is reading in place when only the polling clock advances.
  renderedHistory = snapshot;
}

async function refreshAlerts() {
  const request = ++alertRequest;
  const query = '/api/alerts?limit=20' + (selectedStation ? '&node_id=' + encodeURIComponent(selectedStation) : '');
  try {
    const events = await getJson(query);
    if (request === alertRequest) renderAlertHistory(events);
  } catch (error) {
    if (request !== alertRequest) return;
    el('alert-log-count').textContent = 'History unavailable';
    alertNote('Alert history unavailable. Retrying…');
  }
}

function showHistoryStatus() {
  if (!hydroChart) return;
  const stations = selectedStation ? [selectedStation] : catalog.map(sensor => sensor.Sensor_ID);
  const counts = stations.map(id => historyAvailability.get(id));
  const hasErrors = counts.some(value => value === null);
  if (hasErrors) el('history-status').textContent = 'Some stored history is unavailable. Affected curves are hidden.';
  else if (counts.some(value => value === undefined)) el('history-status').textContent = 'Loading stored observations…';
  else {
    const total = counts.reduce((sum, count) => sum + count, 0);
    el('history-status').textContent = total ? `${total} dated reading${total === 1 ? '' : 's'} · breaks mark gaps in sampling.` : 'No timestamped observations yet.';
  }
}

async function refreshHistory() {
  if (!hydroChart) return;
  await Promise.all(catalog.map(async (sensor, index) => {
    try {
      const rows = await getJson('/api/sensors/history/' + encodeURIComponent(sensor.Sensor_ID) + '?limit=120');
      if (!Array.isArray(rows)) throw new Error('Invalid station history');
      const points = buildHistoryPoints(rows);
      hydroChart.data.datasets[index].data = points;
      historyAvailability.set(sensor.Sensor_ID, points.filter(point => point.y !== null).length);
    } catch (error) {
      hydroChart.data.datasets[index].data = [];
      historyAvailability.set(sensor.Sensor_ID, null);
    }
  }));
  hydroChart.update();
  showHistoryStatus();
}

function selectStation(id) {
  if (id && !catalog.some(sensor => sensor.Sensor_ID === id)) return;
  if (id === selectedStation) return;
  selectedStation = id;
  el('station-filter').value = id;
  el('station-scope').textContent = id ? stationName(id) : 'Latest observations';
  for (const [sensorId, { card }] of cards) card.hidden = Boolean(id && sensorId !== id);
  map?.setSelected(id);
  hydroChart?.setStation(id);
  showHistoryStatus();
  el('alert-log-count').textContent = 'Loading transitions';
  alertNote('Loading alert transitions…');
  return refreshAlerts();
}

async function loadCatalog() {
  const rules = await getJson('/api/sensors/catalog');
  if (!Array.isArray(rules?.stations) || !rules.stations.length) throw new Error('Empty station catalog');
  const stations = new Map(rules.stations.filter(sensor => typeof sensor?.Sensor_ID === 'string' && sensor.Sensor_ID)
    .map(sensor => [sensor.Sensor_ID, sensor]));
  const ids = Array.isArray(rules.expected_sensor_ids) ? new Set(rules.expected_sensor_ids) : stations.keys();
  catalog = [...ids].filter(id => typeof id === 'string' && id).map(id => stations.get(id) || { Sensor_ID: id });
  if (!catalog.length) throw new Error('Empty station catalog');
  // Previously fetched history may still display IDs while catalog lookup was unavailable.
  renderedHistory = null;
  const backendGap = rules.stale_after_seconds;
  const seconds = Number.isFinite(configuredGap) && configuredGap > 0 ? configuredGap : backendGap;
  gapLimitMs = Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 90000;
  const select = el('station-filter');
  for (const sensor of catalog) {
    const option = document.createElement('option');
    option.value = sensor.Sensor_ID;
    option.textContent = sensor.name ? sensor.name + ' · ' + sensor.Sensor_ID : sensor.Sensor_ID;
    select.appendChild(option);
  }
  select.disabled = false;
  if (globalThis.StreamVisuals) hydroChart = StreamVisuals.createChart(el('hydrographChart'), catalog);
  if (!hydroChart) el('history-status').textContent = 'Chart display unavailable.';
  el('sensor-cards').textContent = '';
  catalogReady = true;
}

async function loadMapLocations() {
  try {
    const geo = await getJson('/api/spatial/sensors');
    if (!Array.isArray(geo?.features)) throw new Error('Invalid sensor locations');
    const coordinates = new Map(geo.features.map(feature => [feature?.properties?.Sensor_ID, feature?.geometry?.coordinates]));
    const mapped = [];
    for (const station of catalog) {
      const point = coordinates.get(station.Sensor_ID);
      if (Array.isArray(point) && Number.isFinite(point[0]) && Math.abs(point[0]) <= 180 &&
          Number.isFinite(point[1]) && Math.abs(point[1]) <= 90) {
        mapped.push({ ...station, coordinates: point });
      }
    }
    map?.setCatalog(mapped);
    locationNote = mapped.length === catalog.length ? '' : mapped.length + ' of ' + catalog.length + ' station locations mapped.';
    mapReady = mapped.length === catalog.length;
  } catch (error) {
    locationNote = 'Station map locations unavailable. Readings remain available.';
  }
  el('spatial-status').textContent = [locationNote, bypassNote].filter(Boolean).join(' ');
}

async function loadBypass() {
  try {
    const bypass = await getJson('/api/spatial/bypass');
    const length = bypass?.metadata?.geometry_length_m;
    if (!Number.isFinite(length) || length < 0) throw new Error('Invalid trace');
    map?.setBypass(bypass);
    bypassNote = `Illustrative bypass trace: ${(length / 1000).toFixed(2)} km mapped. Alignment awaits GIS verification.`;
    bypassReady = true;
  } catch (error) {
    bypassNote = 'Bypass mapping unavailable. Station monitoring continues.';
  }
  el('spatial-status').textContent = [locationNote, bypassNote].filter(Boolean).join(' ');
}

async function refreshReplayInvitation() {
  const invitation = el('replay-invitation');
  try {
    const health = await getJson('/health');
    const id = health?.status === 'ok' ? health.simulation_run_id : null;
    if (id !== reviewRunId) {
      reviewRunId = id;
      reviewComplete = false;
      invitation.hidden = true;
    }
    if (!id || reviewComplete) return;
    const review = await getJson('/api/review');
    if (review.run_id !== id || !Array.isArray(review.frames)) {
      invitation.hidden = true;
      return;
    }
    const state = review.status;
    reviewComplete = ['completed', 'cancelled', 'failed', 'not_recorded'].includes(state);
    if (!review.frames.length) {
      invitation.hidden = true;
      return;
    }
    const scene = String(review.scenario || 'Simulation').replaceAll('-', ' ');
    el('replay-title').textContent = state === 'completed' ? 'Completed simulation · ' + scene
      : state === 'running' ? 'Simulation in progress · ' + scene : 'Partial recording · ' + scene;
    const frames = review.frames.length;
    el('replay-evidence').textContent = `${frames} recorded checkpoint${frames === 1 ? '' : 's'}. Explore station decisions and dry-run notifications.`;
    invitation.hidden = false;
  } catch {
    // A lost connection may precede a different server using the same port.
    invitation.hidden = true;
    reviewComplete = false;
  } finally {
    // A slow replay or health request must never delay live station polling.
    setTimeout(refreshReplayInvitation, 10000);
  }
}

async function poll() {
  try {
    if (!catalogReady) {
      try {
        await loadCatalog();
      } catch (error) {
        setBadge('SENSOR CATALOG UNAVAILABLE');
        el('assessment-note').textContent = 'Waiting for the list of configured stations.';
        el('sensor-cards').textContent = 'Station catalog could not be loaded. Retrying…';
        el('last-update').textContent = 'Station catalog unavailable · retrying';
        await refreshAlerts();
        return;
      }
    }
    const updateReadings = async () => {
      try { renderStatus(await getJson('/api/sensors/status')); }
      catch (error) { renderStatus({}, false); }
    };
    await Promise.all([updateReadings(), refreshHistory(), refreshAlerts(),
      mapReady ? null : loadMapLocations(), bypassReady ? null : loadBypass()]);
  } finally {
    setTimeout(poll, 2000);
  }
}

function initialize() {
  if (globalThis.StreamVisuals) map = StreamVisuals.createMap(el('map'), selectStation);
  else el('map').textContent = 'Map display unavailable.';
  el('station-filter').addEventListener('change', event => selectStation(event.target.value));
  return Promise.all([poll(), refreshReplayInvitation()]);
}

const initialLoad = initialize();
