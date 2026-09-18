import test from 'node:test';
import assert from 'node:assert/strict';
import {renderReviewCharts} from '../../src/fitness_bot/web/review-charts.mjs';

// Minimal DOM boundary: text is never parsed, and any HTML assignment fails.
class Element {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.children = []; this.attributes = {}; this.listeners = {}; this.text = ''; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(node => node.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML parsing is forbidden'); }
}
const doc = {createElement: tag => new Element(tag, doc), createElementNS: (_, tag) => new Element(tag, doc)};
const root = () => new Element('section', doc);
const nodes = (el, tag) => [el, ...el.children.flatMap(child => nodes(child, tag))].filter(node => node.tagName === tag);
const point = (label, value, at) => ({label, value, at});
const chart = (kind, points, extra = {}) => ({id: 'test', kind, points, title: '训练分布', description: '所选记录', unit: '组', ...extra});

test('timeline uses elapsed dates, sorts without mutating input, and exposes exact dates', () => {
  const host = root();
  const points = [point('第三次', 1, '2026-09-11T00:00:00Z'), point('第一次', 1, '2026-09-01T00:00:00Z'), point('第二次', 1, '2026-09-02T00:00:00Z')];
  renderReviewCharts(host, [chart('timeline', points)]);
  const circles = nodes(host, 'circle');
  const xs = circles.map(node => Number(node.attributes.cx));
  assert.equal(circles.length, 3);
  assert.ok(Math.abs((xs[1] - xs[0]) / (xs[2] - xs[0]) - .1) < 1e-9);
  assert.equal(points[0].label, '第三次');
  assert.match(host.textContent, /2026-09-11T00:00:00Z/);
});

test('all-zero columns and bars retain zero baselines and finite SVG dimensions', () => {
  for (const kind of ['columns', 'bars']) {
    const host = root();
    renderReviewCharts(host, [chart(kind, [point('甲', 0), point('乙', 0)])]);
    const rects = nodes(host, 'rect');
    assert.equal(rects.length, 2);
    for (const rect of rects) {
      assert.equal(Number(rect.attributes[kind === 'bars' ? 'width' : 'height']), 0);
      for (const key of ['x', 'y', 'width', 'height']) assert.ok(Number.isFinite(Number(rect.attributes[key])));
    }
    assert.match(host.textContent, /0 组/);
  }
});

test('unknown, empty and invalid measurements are skipped safely', () => {
  const host = root();
  renderReviewCharts(host, [null, chart('unknown', [point('bad', 1)]), chart('bars', []), chart('bars', [point('bad', NaN), point('bad', -1), point('bad', Infinity), point('bad', '2'), point('valid', 2)]), chart('timeline', [point('bad date', 1, 'not-a-date')])]);
  assert.equal(nodes(host, 'figure').length, 1);
  assert.equal(nodes(host, 'rect').length, 1);
  assert.match(host.textContent, /valid/);
  assert.doesNotMatch(host.textContent, /bad/);
});

test('truncation remains visible and rendering is bounded', () => {
  const host = root();
  renderReviewCharts(host, [chart('bars', [point('甲', 2)], {truncated: true}), chart('columns', Array.from({length: 500}, (_, i) => point(String(i), i)))]);
  assert.equal(nodes(host, 'figure').length, 2);
  assert.match(nodes(host, 'figure')[0].textContent, /部分/);
  assert.match(nodes(host, 'figure')[1].textContent, /部分/);
  assert.ok(nodes(host, 'rect').length <= 201);
});

test('untrusted labels remain text and focus and hover expose values with units', () => {
  const host = root();
  const label = '<img src=x onerror=alert(1)>';
  renderReviewCharts(host, [chart('bars', [point(label, 12)], {title: '<script>bad()</script>'})]);
  assert.equal(nodes(host, 'img').length, 0);
  assert.equal(nodes(host, 'script').length, 0);
  assert.match(host.textContent, /<script>bad\(\)<\/script>/);
  assert.equal(nodes(host, 'details').length, 1);
  assert.equal(nodes(host, 'table').length, 1);
  const mark = nodes(host, 'g').find(node => node.attributes.tabindex === '0');
  assert.match(mark.attributes['aria-label'], /12 组/);
  mark.listeners.focus();
  assert.match(nodes(host, 'output')[0].textContent, /12 组/);
  mark.listeners.mouseenter();
  assert.match(nodes(host, 'output')[0].textContent, /<img src=x onerror=alert\(1\)>/);
});

test('single-date timelines stay centered and malformed optional dates never break rendering', () => {
  const host = root();
  renderReviewCharts(host, [chart('timeline', [point('same', 1, '2026-09-01T00:00:00Z')]), chart('columns', [point('safe', 3, {bad: true})])]);
  assert.equal(Number(nodes(host, 'circle')[0].attributes.cx), 280);
  assert.equal(nodes(host, 'figure').length, 2);
});

test('small positive measurements are never labeled zero', () => {
  const host = root();
  renderReviewCharts(host, [chart('bars', [point('small', 0.0001)])]);
  assert.match(nodes(host, 'g').find(node => node.attributes.tabindex === '0').attributes['aria-label'], /0\.0001 组/);
});

test('timeline and column axes use server display dates across UTC date boundaries', () => {
  for (const kind of ['timeline', 'columns']) {
    const host = root();
    const points = [point('2026-09-02', 1, '2026-09-01T18:00:00Z'), point('2026-09-04', 2, '2026-09-03T18:00:00Z')];
    renderReviewCharts(host, [chart(kind, points)]);
    const axisLabels = nodes(host, 'text').map(node => node.textContent);
    assert.ok(axisLabels.includes('2026-09-02'));
    assert.ok(axisLabels.includes('2026-09-04'));
    assert.ok(!axisLabels.includes('2026-09-01'));
    assert.match(nodes(host, 'table')[0].textContent, /2026-09-01T18:00:00Z/);
  }
});
