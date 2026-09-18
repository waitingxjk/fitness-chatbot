// Charts visualize server-owned measurements; all incoming strings remain DOM text.
const SVG = 'http://www.w3.org/2000/svg';
const MAX_POINTS = 200;
const MAX_CHARTS = 12;
const WIDTH = 560;
const number = value => Number.isInteger(value) ? value.toLocaleString('zh-CN') : String(value);
const text = value => typeof value === 'string' ? value : '';
const shortLabel = value => Array.from(value).length > 5 ? `${Array.from(value).slice(0, 4).join('')}…` : value;

function element(doc, tag, content, className) {
  const node = doc.createElement(tag);
  if (content != null) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function shape(doc, tag, attributes, content) {
  const node = doc.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (content != null) node.textContent = content;
  return node;
}

function validPoints(chart) {
  if (!Array.isArray(chart.points)) return [];
  return chart.points.filter(point => point && typeof point.label === 'string'
    && typeof point.value === 'number' && Number.isFinite(point.value) && point.value >= 0
    && (chart.kind !== 'timeline' || (typeof point.at === 'string' && Number.isFinite(Date.parse(point.at)))));
}

function pointDescription(point, unit) {
  return `${point.label}${text(point.at) ? ` · ${point.at}` : ''}：${number(point.value)}${unit ? ` ${unit}` : ''}`;
}

function pointGroup(doc, svg, point, unit, output) {
  const label = pointDescription(point, unit);
  const group = shape(doc, 'g', {class: 'review-chart-point', tabindex: 0, role: 'img', 'aria-label': label});
  group.append(shape(doc, 'title', {}, label));
  for (const event of ['focus', 'mouseenter', 'click']) group.addEventListener(event, () => { output.textContent = label; });
  svg.append(group);
  return group;
}

function timeline(doc, svg, points, unit, output) {
  const left = 28, right = WIDTH - 28, y = 48;
  const first = Date.parse(points[0].at), last = Date.parse(points.at(-1).at);
  svg.append(shape(doc, 'line', {x1: left, x2: right, y1: y, y2: y, class: 'review-chart-axis'}));
  for (const point of points) {
    const x = last === first ? WIDTH / 2 : left + (Date.parse(point.at) - first) / (last - first) * (right - left);
    pointGroup(doc, svg, point, unit, output).append(shape(doc, 'circle', {cx: x, cy: y, r: 5}));
  }
  svg.append(shape(doc, 'text', {x: left, y: 84, class: 'review-chart-axis-label'}, points[0].label));
  if (last !== first) svg.append(shape(doc, 'text', {x: right, y: 84, 'text-anchor': 'end', class: 'review-chart-axis-label'}, points.at(-1).label));
}

function columns(doc, svg, points, unit, output) {
  const left = 40, right = WIDTH - 16, baseline = 164, top = 24;
  const max = Math.max(1, ...points.map(point => point.value));
  const step = (right - left) / points.length;
  svg.append(shape(doc, 'line', {x1: left, x2: right, y1: baseline, y2: baseline, class: 'review-chart-axis'}));
  svg.append(shape(doc, 'text', {x: left - 8, y: baseline + 4, 'text-anchor': 'end', class: 'review-chart-axis-label'}, '0'));
  svg.append(shape(doc, 'text', {x: left, y: 14, class: 'review-chart-axis-label'}, `${number(max)} ${unit}`));
  for (const [index, point] of points.entries()) {
    const x = left + step * (index + .5), height = point.value / max * (baseline - top);
    const group = pointGroup(doc, svg, point, unit, output);
    group.append(shape(doc, 'rect', {x: x - step * .3, y: baseline - height, width: step * .6, height, rx: 2}));
    if (point.value === 0) group.append(shape(doc, 'line', {x1: x - step * .3, x2: x + step * .3, y1: baseline, y2: baseline}));
    if (points.length <= 10) group.append(shape(doc, 'text', {x, y: baseline - height - 7, 'text-anchor': 'middle', class: 'review-chart-value'}, number(point.value)));
    // Full local dates at the ends stay legible even when mobile labels enlarge.
    if (index === 0 || index === points.length - 1) {
      const anchor = points.length === 1 ? 'middle' : index === 0 ? 'start' : 'end';
      const labelX = points.length === 1 ? x : index === 0 ? left : right;
      svg.append(shape(doc, 'text', {x: labelX, y: baseline + 30, 'text-anchor': anchor, class: 'review-chart-axis-label'}, point.label));
    }
  }
}

function bars(doc, svg, points, unit, output) {
  const left = 150, right = WIDTH - 65, top = 28, row = 36;
  const max = Math.max(1, ...points.map(point => point.value));
  svg.append(shape(doc, 'line', {x1: left, x2: left, y1: top - 8, y2: top + row * points.length - 12, class: 'review-chart-axis'}));
  svg.append(shape(doc, 'text', {x: left, y: 13, class: 'review-chart-axis-label'}, '0'));
  for (const [index, point] of points.entries()) {
    const y = top + row * index, width = point.value / max * (right - left);
    const group = pointGroup(doc, svg, point, unit, output);
    group.append(shape(doc, 'text', {x: left - 12, y: y + 14, 'text-anchor': 'end', class: 'review-chart-axis-label'}, shortLabel(point.label)));
    group.append(shape(doc, 'rect', {x: left, y, width, height: 20, rx: 3}));
    if (point.value === 0) group.append(shape(doc, 'line', {x1: left, x2: left, y1: y, y2: y + 20}));
    group.append(shape(doc, 'text', {x: left + width + 8, y: y + 14, class: 'review-chart-value'}, number(point.value)));
  }
}

function dataTable(doc, points, unit) {
  const details = element(doc, 'details', null, 'review-chart-data');
  details.append(element(doc, 'summary', `查看数据（${points.length} 条）`));
  const scroll = element(doc, 'div', null, 'review-chart-table-scroll');
  const table = element(doc, 'table');
  const head = element(doc, 'thead'), header = element(doc, 'tr');
  for (const label of ['记录 / 部位', '记录时间', `数值${unit ? `（${unit}）` : ''}`]) {
    const cell = element(doc, 'th', label); cell.setAttribute('scope', 'col'); header.append(cell);
  }
  head.append(header); table.append(head);
  const body = element(doc, 'tbody');
  for (const point of points) {
    const row = element(doc, 'tr');
    row.append(element(doc, 'td', point.label), element(doc, 'td', text(point.at) || '—'), element(doc, 'td', String(point.value)));
    body.append(row);
  }
  table.append(body); scroll.append(table); details.append(scroll);
  return details;
}

export function renderReviewCharts(root, charts) {
  if (!Array.isArray(charts)) return;
  const doc = root.ownerDocument;
  const grid = element(doc, 'div', null, 'review-charts');
  for (const chart of charts.slice(0, MAX_CHARTS)) {
    if (!chart || !['timeline', 'columns', 'bars'].includes(chart.kind)) continue;
    const valid = validPoints(chart);
    if (!valid.length) continue;
    const points = valid.slice(0, MAX_POINTS);
    if (chart.kind === 'timeline') points.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    const unit = text(chart.unit), title = text(chart.title) || '训练记录';
    const figure = element(doc, 'figure', null, `review-chart review-chart-${chart.kind}`);
    const caption = element(doc, 'figcaption');
    caption.append(element(doc, 'h4', title));
    if (text(chart.description)) caption.append(element(doc, 'p', chart.description));
    caption.append(element(doc, 'small', `${points.length} 条记录${unit ? ` · 单位：${unit}` : ''}${chart.kind === 'timeline' ? ' · 按实际日期间隔排列' : ''}`));
    figure.append(caption);
    const output = element(doc, 'output', '轻触、悬停或聚焦图形可查看数值', 'review-chart-readout');
    output.setAttribute('aria-live', 'polite');
    const height = chart.kind === 'timeline' ? 104 : chart.kind === 'bars' ? 36 * points.length + 40 : 208;
    const svg = shape(doc, 'svg', {viewBox: `0 0 ${WIDTH} ${height}`, class: 'review-chart-svg', role: 'group', 'aria-label': `${title}${unit ? `，单位：${unit}` : ''}`});
    ({timeline, columns, bars})[chart.kind](doc, svg, points, unit, output);
    figure.append(svg, output);
    if (chart.truncated || valid.length > MAX_POINTS) figure.append(element(doc, 'p', '仅展示部分记录；图表不代表完整训练历史。', 'review-chart-note'));
    figure.append(dataTable(doc, points, unit)); grid.append(figure);
  }
  if (grid.children.length) root.append(grid);
}
