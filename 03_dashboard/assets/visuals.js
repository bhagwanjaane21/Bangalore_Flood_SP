/* Local, north-up GIS view and water-depth chart for the STREAM dashboard. */
(() => {
  'use strict';

  const svgNS = 'http://www.w3.org/2000/svg';
  const levels = { GREEN: '#197357', YELLOW: '#956300', ORANGE: '#b25117', RED: '#ad3638', UNKNOWN: '#5b696e' };
  const traces = ['#10666a', '#9b590c', '#75539d'];
  const dashes = ['', '7 3', '2 3'];
  const sites = { SENSOR_01: 'Lake sluice', SENSOR_02: 'Mid-reach culvert', SENSOR_03: 'Manyata Gate 4' };
  // Original prototype paths from the teammate's Gate 4 overlay, as [longitude, latitude].
  const detours = [
    { title: 'Gate 4 vicinity', points: [[77.6195, 13.0515], [77.6183, 13.0485], [77.6168, 13.0458]] },
    { title: 'Illustrative path toward Hebbal', points: [[77.6183, 13.0485], [77.6135, 13.0472], [77.6050, 13.0455], [77.5960, 13.0442]] },
    { title: 'Illustrative northern path', points: [[77.6155, 13.0535], [77.6110, 13.0575], [77.6030, 13.0610], [77.5945, 13.0635]] },
  ];

  function html(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function svg(tag, attributes = {}, text) {
    const element = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function validPoint(point) {
    return Array.isArray(point) && Number.isFinite(point[0]) && Math.abs(point[0]) <= 180
      && Number.isFinite(point[1]) && Math.abs(point[1]) <= 90;
  }

  // A local equirectangular projection preserves longitude scale at the corridor latitude.
  function fitCorridor(points, width, height) {
    const xs = points.map(point => point[0]);
    const ys = points.map(point => point[1]);
    const west = Math.min(...xs), east = Math.max(...xs);
    const south = Math.min(...ys), north = Math.max(...ys);
    const centre = [(west + east) / 2, (south + north) / 2];
    const metreLat = Math.PI * 6371000 / 180;
    const metreLon = metreLat * Math.cos(centre[1] * Math.PI / 180);
    // Labels face inward. Keeping the outer gutter small leaves enough separation
    // between the three eastern stations when the western detours expand the view.
    const scale = Math.min((width - 44) / Math.max(350, (east - west) * metreLon),
      (height - 84) / Math.max(350, (north - south) * metreLat));
    return {
      centre, scale,
      pixel: ([lon, lat]) => [width / 2 + (lon - centre[0]) * metreLon * scale,
        height / 2 - (lat - centre[1]) * metreLat * scale],
    };
  }

  function niceStep(value) {
    const power = 10 ** Math.floor(Math.log10(value));
    return ([1, 2, 5, 10].find(step => step * power >= value) || 10) * power;
  }

  const stationLabel = id => /^SENSOR_\d+$/.test(id) ? 'Station ' + id.slice(7) : id;

  function markerState(reading) {
    if (!reading) return { level: 'UNKNOWN', label: 'No data' };
    if (reading.data_status !== 'fresh' || !Object.hasOwn(levels, reading.alert_level)) {
      return { level: 'UNKNOWN', label: reading.data_status === 'stale' ? 'Stale' : 'Unknown' };
    }
    return { level: reading.alert_level, label: reading.alert_level };
  }

  function createMap(container, onSelect) {
    const graphic = svg('svg', { class: 'corridor-plot', role: 'group', 'aria-label': 'Proposed sensor stations and illustrative bypass, north up' });
    const controls = html('div', 'corridor-controls');
    const label = html('label', 'corridor-toggle');
    const checkbox = html('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.disabled = true;
    label.append(checkbox, html('span', '', 'Bypass trace'));
    const orderKey = html('span', 'corridor-order-key', 'Station order');
    orderKey.title = 'Upstream to downstream station sequence';
    orderKey.hidden = true;
    const street = html('a', 'street-link', 'Street map ↗');
    street.target = '_blank';
    street.rel = 'noopener noreferrer';
    street.setAttribute('aria-label', 'Open this area on OpenStreetMap in a new tab');
    const routeKey = html('span', 'corridor-route-key');
    routeKey.append(html('span', 'vicinity-key', 'Gate 4 vicinity'), html('span', 'detour-key', 'Illustrative paths'));
    routeKey.hidden = true;
    controls.append(label, orderKey, routeKey, street);
    container.replaceChildren(graphic, controls);

    let sensors = [], bypass = [], status = {}, selected = '', routesVisible = false;
    let nodes = new Map();
    let centre;
    function hasFreshGateAlert() {
      return sensors.some(site => site.Sensor_ID === 'SENSOR_03') && markerState(status.SENSOR_03).level === 'RED';
    }
    function updateSelection() {
      for (const [id, marker] of nodes) marker.group.setAttribute('aria-pressed', String(id === selected));
      const location = sensors.find(sensor => sensor.Sensor_ID === selected)?.coordinates || centre;
      street.hidden = !location;
      if (location) street.href = 'https://www.openstreetmap.org/#map=16/' + location[1].toFixed(5) + '/' + location[0].toFixed(5);
    }
    function paint() {
      for (const sensor of sensors) {
        const marker = nodes.get(sensor.Sensor_ID);
        if (!marker) continue;
        const reading = status[sensor.Sensor_ID];
        const { level, label: stateLabel } = markerState(reading);
        marker.dot.setAttribute('fill', levels[level]);
        marker.state.textContent = stateLabel;
        const description = stationLabel(sensor.Sensor_ID) + ', ' + (sensor.name || sites[sensor.Sensor_ID] || sensor.Sensor_ID) + ', ' + stateLabel;
        marker.group.setAttribute('aria-label', description);
        marker.title.textContent = description + ' · ' + sensor.coordinates[1].toFixed(5) + '° N, ' + sensor.coordinates[0].toFixed(5) + '° E';
      }
      updateSelection();
    }
    function draw() {
      routeKey.hidden = !routesVisible;
      orderKey.hidden = true;
      const focusId = [...nodes.entries()].find(([, node]) => node.group === document.activeElement)?.[0];
      const width = Math.max(220, Math.round(graphic.clientWidth || container.clientWidth || 520));
      const height = Math.max(190, Math.round(graphic.clientHeight || 265));
      graphic.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
      graphic.replaceChildren();
      nodes = new Map();
      const visibleLines = checkbox.checked ? bypass : [];
      const points = [...sensors.map(sensor => sensor.coordinates), ...visibleLines.flat()];
      if (routesVisible) points.push(...detours.flatMap(route => route.points));
      if (!points.length) {
        centre = undefined;
        graphic.append(svg('text', { x: '50%', y: '50%', class: 'visual-placeholder', 'text-anchor': 'middle' }, 'Awaiting station locations'));
        updateSelection();
        return;
      }
      const projection = fitCorridor(points, width, height);
      centre = projection.centre;
      const gridMetres = niceStep(37 / projection.scale);
      const gridSize = gridMetres * projection.scale;
      for (let x = (width / 2) % gridSize; x < width; x += gridSize) {
        graphic.append(svg('line', { x1: x, x2: x, y1: 0, y2: height, class: 'corridor-grid' }));
      }
      for (let y = (height / 2) % gridSize; y < height; y += gridSize) {
        graphic.append(svg('line', { x1: 0, x2: width, y1: y, y2: y, class: 'corridor-grid' }));
      }
      function path(coords, className, title) {
        const line = svg('polyline', { points: coords.map(p => projection.pixel(p).map(n => n.toFixed(2)).join(',')).join(' '), class: className });
        line.append(svg('title', {}, title));
        graphic.append(line);
      }
      visibleLines.forEach(line => path(line, 'corridor-bypass', 'Illustrative bypass alignment'));
      if (routesVisible) detours.forEach((route, i) => path(route.points, i === 0 ? 'corridor-vicinity' : 'corridor-detour', route.title + ' · road passability unverified'));

      // Join only adjacent proposed stations. These guides communicate order, not channel geometry.
      const ordered = Object.keys(sites).map(id => sensors.find(site => site.Sensor_ID === id));
      for (let i = 1; i < ordered.length; i++) {
        if (!ordered[i - 1] || !ordered[i]) continue;
        const [x1, y1] = projection.pixel(ordered[i - 1].coordinates);
        const [x2, y2] = projection.pixel(ordered[i].coordinates);
        const link = svg('g', { class: 'corridor-order' });
        link.append(svg('title', {}, 'Station order · ' + sites[ordered[i - 1].Sensor_ID] + ' to ' + sites[ordered[i].Sensor_ID]),
          svg('line', { x1, y1, x2, y2 }));
        const heading = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
        link.append(svg('path', { d: 'M-3 -3 L1 0 L-3 3',
          transform: 'translate(' + (x1 + x2) / 2 + ' ' + (y1 + y2) / 2 + ') rotate(' + heading + ')' }));
        graphic.append(link);
        orderKey.hidden = false;
      }

      for (const sensor of sensors) {
        const id = sensor.Sensor_ID;
        const [x, y] = projection.pixel(sensor.coordinates);
        const group = svg('g', { class: 'corridor-station', transform: 'translate(' + x + ' ' + y + ')', role: 'button', tabindex: 0, 'data-station': id });
        const activate = () => onSelect?.(selected === id ? '' : id);
        group.addEventListener('click', activate);
        group.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
        });
        const title = svg('title');
        const ring = svg('circle', { r: 14, class: 'station-ring' });
        const dot = svg('circle', { r: 9, class: 'station-dot' });
        const number = svg('text', { 'text-anchor': 'middle', y: 3.1, class: 'station-number' }, id.replace(/^SENSOR_/, '').slice(-2));
        const alignLeft = x > width / 2;
        const labelX = alignLeft ? -18 : 18;
        const anchor = alignLeft ? 'end' : 'start';
        const name = svg('text', { x: labelX, y: -1, 'text-anchor': anchor, class: 'station-place' }, sites[id] || stationLabel(id));
        const state = svg('text', { x: labelX, y: 11, 'text-anchor': anchor, class: 'station-state' });
        group.append(title, ring, dot, number, name, state);
        nodes.set(id, { group, dot, state, title });
        graphic.append(group);
      }
      const scaleY = height - 14;
      graphic.append(svg('path', { d: 'M14 ' + (scaleY - 4) + 'v4h' + gridSize.toFixed(2) + 'v-4', class: 'corridor-scale' }),
        svg('text', { x: 14, y: scaleY - 8, class: 'corridor-caption' }, gridMetres >= 1000 ? gridMetres / 1000 + ' km' : gridMetres + ' m'),
        svg('path', { d: 'M20 18l-4 11 4-3 4 3Z', fill: '#426660' }),
        svg('text', { x: 20, y: 13, 'text-anchor': 'middle', class: 'corridor-caption' }, 'N'),
        svg('text', { x: width - 12, y: scaleY, 'text-anchor': 'end', class: 'corridor-caption' }, centre[1].toFixed(3) + '° N   ' + centre[0].toFixed(3) + '° E'));
      paint();
      if (focusId) nodes.get(focusId)?.group.focus({ preventScroll: true });
    }
    checkbox.addEventListener('change', draw);
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(draw).observe(graphic);
    draw();
    return {
      setCatalog(items) {
        sensors = items.filter(item => typeof item?.Sensor_ID === 'string' && validPoint(item.coordinates));
        routesVisible = hasFreshGateAlert();
        draw();
      },
      setBypass(collection) {
        bypass = (collection?.features || []).map(feature => feature?.geometry)
          .filter(geometry => geometry?.type === 'LineString' && Array.isArray(geometry.coordinates)
            && geometry.coordinates.length >= 2 && geometry.coordinates.every(validPoint))
          .map(geometry => geometry.coordinates);
        checkbox.disabled = !bypass.length;
        draw();
      },
      setStatus(readings) {
        status = readings || {};
        const active = hasFreshGateAlert();
        if (active !== routesVisible) { routesVisible = active; draw(); }
        else paint();
      },
      setSelected(id) { selected = id; updateSelection(); },
      getRoutesVisible: () => routesVisible,
    };
  }

  const dateTime = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  const clockTime = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  const dateOnly = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' });
  function depthText(value) {
    if (value >= 1000) return value.toExponential(1);
    return String(Number(value.toFixed(3)));
  }

  function createChart(container, catalog) {
    const data = { datasets: catalog.map(sensor => ({ label: sensor.Sensor_ID, data: [] })) };
    const tools = html('div', 'hydro-tools');
    const legend = html('div', 'hydro-legend');
    const thresholdLabel = html('label', 'hydro-toggle');
    const toggle = html('input');
    toggle.type = 'checkbox';
    thresholdLabel.append(toggle, html('span', '', 'Depth thresholds'));
    tools.append(legend, thresholdLabel);
    const thresholdKey = html('div', 'hydro-threshold-key');
    const graphic = svg('svg', { class: 'hydro-plot', role: 'img', 'aria-label': 'Stored water-depth observations by station, in metres' });
    const inspector = html('div', 'hydro-inspector');
    const sample = html('span', 'sample-details');
    const slider = html('input');
    slider.type = 'range';
    slider.min = '0';
    slider.step = '1';
    slider.setAttribute('aria-label', 'Inspect stored observations by time');
    inspector.append(html('span', 'inspection-label', 'Observation time'), sample, slider);
    container.replaceChildren(tools, graphic, thresholdKey, inspector);
    let selected = '', selectedTime = null, times = [], visible = [], plotX, plotY, focusLine, focusPoints;
    let signature = '';

    function inspect(index) {
      const time = times[index];
      if (time === undefined) return;
      focusPoints.replaceChildren();
      const observations = visible.flatMap(({ series, index: seriesIndex }) => {
        const point = series.data.find(p => p && p.x === time && Number.isFinite(p.y) && p.y >= 0);
        if (point) focusPoints.append(svg('circle', { cx: plotX(time), cy: plotY(point.y), r: 3.6,
          fill: traces[seriesIndex % traces.length], stroke: 'white', 'stroke-width': 1.4 }));
        return point ? [stationLabel(series.label) + ' · ' + point.y + ' m'] : [];
      });
      const description = dateTime.format(time) + ' IST  |  ' + observations.join('  ·  ');
      sample.textContent = description;
      slider.value = String(index);
      slider.setAttribute('aria-valuetext', description);
      focusLine.setAttribute('x1', plotX(time));
      focusLine.setAttribute('x2', plotX(time));
    }
    function selectObservation(index) {
      selectedTime = index === times.length - 1 ? null : times[index];
      inspect(index);
    }
    slider.addEventListener('input', () => selectObservation(Number(slider.value)));
    graphic.addEventListener('click', event => {
      if (!times.length) return;
      const bounds = graphic.getBoundingClientRect();
      if (!bounds.width) return;
      const x = (event.clientX - bounds.left) * graphic.viewBox.baseVal.width / bounds.width;
      const closest = times.reduce((best, time, index) =>
        Math.abs(plotX(time) - x) < Math.abs(plotX(times[best]) - x) ? index : best, 0);
      selectObservation(closest);
    });
    toggle.addEventListener('change', draw);
    function draw() {
      visible = data.datasets.map((series, index) => ({ series, index })).filter(({ series }) => !selected || selected === series.label);
      legend.replaceChildren();
      visible.forEach(({ series, index }) => {
        const key = html('span', 'hydro-key');
        const swatch = svg('svg', { width: 22, height: 7, 'aria-hidden': 'true' });
        swatch.append(svg('line', { x1: 0, x2: 22, y1: 4, y2: 4, stroke: traces[index % traces.length], 'stroke-width': 2.5, 'stroke-dasharray': dashes[index % dashes.length] }));
        key.append(swatch, html('span', '', stationLabel(series.label)));
        legend.append(key);
      });
      const width = Math.max(220, Math.round(graphic.clientWidth || container.clientWidth || 500));
      const height = Math.max(125, Math.round(graphic.clientHeight || 165));
      graphic.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
      graphic.replaceChildren();
      const valid = point => point && Number.isFinite(point.x) && Math.abs(point.x) <= 8640000000000000 && Number.isFinite(point.y) && point.y >= 0;
      const observations = visible.flatMap(({ series }) => series.data.filter(valid));
      times = [...new Set(observations.map(point => point.x))].sort((a, b) => a - b);
      const rules = catalog.find(station => station.Sensor_ID === selected)?.depth_thresholds_m;
      const bands = ['YELLOW', 'ORANGE', 'RED'];
      const usable = rules && bands.every((band, i) => Number.isFinite(rules[band]) && rules[band] >= 0 &&
        (i === 0 || rules[band] > rules[bands[i - 1]]));
      thresholdLabel.hidden = !usable || !times.length;
      const showThresholds = !thresholdLabel.hidden && toggle.checked;
      thresholdKey.hidden = !showThresholds;
      thresholdKey.replaceChildren();
      if (showThresholds) {
        for (const band of bands) {
          const key = html('span', 'threshold-key-item', band.charAt(0) + band.slice(1).toLowerCase() + ' ' + depthText(rules[band]) + ' m');
          key.setAttribute('data-level', band);
          thresholdKey.append(key);
        }
        thresholdKey.append(html('small', '', 'Depth rules · rise and recovery also affect alerts.'));
      }
      inspector.hidden = !times.length;
      if (!times.length) {
        graphic.append(svg('text', { x: '50%', y: '50%', class: 'visual-placeholder', 'text-anchor': 'middle' }, 'No timestamped readings to plot'));
        return;
      }
      const first = times[0], last = times[times.length - 1];
      const left = 49, right = width - 12, top = 18, bottom = height - 25;
      const peak = Math.max(0.1, showThresholds ? rules.RED : 0, ...observations.map(point => point.y));
      const step = niceStep(peak / 4);
      const axisTop = Math.ceil(peak / step) * step;
      const yMax = Number.isFinite(axisTop) ? axisTop : peak;
      plotX = time => first === last ? (left + right) / 2 : left + (time - first) / (last - first) * (right - left);
      plotY = depth => bottom - (depth / yMax) * (bottom - top);
      for (let i = 0; i <= 4; i += 1) {
        const depth = yMax * (i / 4);
        const y = plotY(depth);
        graphic.append(svg('line', { x1: left, x2: right, y1: y, y2: y, class: 'hydro-grid' }),
          svg('text', { x: left - 7, y: y + 3, 'text-anchor': 'end', class: 'hydro-label' }, depthText(depth)));
      }
      graphic.append(svg('text', { x: left, y: 10, class: 'hydro-label' }, 'm'));
      if (showThresholds) {
        for (const band of bands) {
          const y = plotY(rules[band]);
          const line = svg('line', { x1: left, y1: y, x2: right, y2: y, stroke: levels[band],
            class: 'depth-reference', 'data-threshold': band });
          line.append(svg('title', {}, band + ' depth threshold · ' + depthText(rules[band]) + ' m'));
          graphic.append(line);
        }
      }
      const ticks = first === last ? 1 : width < 400 ? 2 : 3;
      for (let i = 0; i < ticks; i += 1) {
        const time = ticks === 1 ? first : first + (last - first) * i / (ticks - 1);
        const anchor = ticks === 1 ? 'middle' : i === 0 ? 'start' : i === ticks - 1 ? 'end' : 'middle';
        const label = last - first >= 86400000 ? dateOnly.format(time) : clockTime.format(time);
        graphic.append(svg('text', { x: plotX(time), y: bottom + 17, 'text-anchor': anchor, class: 'hydro-label' }, label));
      }
      for (const { series, index } of visible) {
        const color = traces[index % traces.length];
        let segment = [];
        function stroke() {
          if (segment.length === 1) graphic.append(svg('circle', { cx: plotX(segment[0].x), cy: plotY(segment[0].y), r: 3, fill: color, 'data-series': series.label }));
          if (segment.length > 1) {
            const points = segment.map(p => plotX(p.x).toFixed(2) + ',' + plotY(p.y).toFixed(2)).join(' ');
            graphic.append(svg('polyline', { points, fill: 'none', stroke: color, 'stroke-width': 2.4,
              'stroke-linejoin': 'round', 'stroke-dasharray': dashes[index % dashes.length], 'data-series': series.label }));
          }
          segment = [];
        }
        for (const point of series.data) { if (valid(point)) segment.push(point); else stroke(); }
        stroke();
      }
      focusLine = svg('line', { y1: top, y2: bottom, class: 'hydro-cursor' });
      focusPoints = svg('g', { class: 'hydro-focus-points', 'aria-hidden': 'true' });
      graphic.append(focusLine, focusPoints);
      slider.max = String(times.length - 1);
      slider.disabled = times.length === 1;
      let index = selectedTime === null ? times.length - 1 : times.findIndex(time => time >= selectedTime);
      if (index < 0) index = times.length - 1;
      inspect(index);
    }
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(draw).observe(graphic);
    draw();
    return {
      data,
      update() {
        const next = JSON.stringify(data.datasets);
        if (next !== signature) { signature = next; draw(); }
      },
      setStation(id) {
        if (selected === id) return;
        selected = id;
        selectedTime = null;
        draw();
      },
    };
  }

  globalThis.StreamVisuals = { createMap, createChart };
})();
