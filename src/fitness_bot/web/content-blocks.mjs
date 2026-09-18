import {renderMarkdown} from './markdown.mjs';
import {renderReviewCharts} from './review-charts.mjs';

const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 128 && !['__proto__','constructor','prototype'].includes(value);
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const emptyReport = () => ({schema_version:1,outline:[],blocks:[],artifacts:{},datasets:{},facts:{}});
function parts(values) {
  return values.slice(0,512).map(value => {
    if (value?.type === 'text' && typeof value.text === 'string') return {type:'text',text:value.text};
    if (value?.type === 'fact_ref' && validId(value.fact_id)) return {type:'fact_ref',fact_id:value.fact_id};
    return {type:'text',text:'[数据暂不可用]'};
  });
}
function blocks(values, isOutline = false) {
  const seen = new Set();
  return (Array.isArray(values) ? values : []).filter(value => {
    if (!object(value) || !validId(value.id) || seen.has(value.id) || !['markdown','chart_ref'].includes(value.type)) return false;
    seen.add(value.id); return true;
  }).slice(0,128).map(value => value.type === 'markdown'
    ? {id:value.id,type:value.type,...(isOutline ? {} : {content:typeof value.content === 'string' ? value.content : '',...(Array.isArray(value.parts) ? {parts:parts(value.parts)} : {})})}
    : {id:value.id,type:value.type,artifact_id:validId(value.artifact_id) ? value.artifact_id : ''});
}
function registry(value) {
  return Object.fromEntries(Object.entries(object(value) ? value : {}).filter(([id,item]) => validId(id) && object(item)).map(([id,item]) => [id,structuredClone(item)]));
}
export function reportFromResponse(response) {
  if (response?.schema_version !== 1 || !Array.isArray(response.blocks)) return null;
  return {schema_version:1,outline:blocks(response.outline,true),blocks:blocks(response.blocks),artifacts:registry(response.artifacts),datasets:registry(response.datasets),facts:registry(response.facts)};
}
export function orderedBlocks(report) {
  const positions = new Map(report.outline.map((block,index) => [block.id,index]));
  return [...report.blocks].sort((a,b) => (positions.get(a.id) ?? Infinity) - (positions.get(b.id) ?? Infinity));
}
export function reportText(report) {
  return orderedBlocks(report).filter(block => block.type === 'markdown').map(block => blockText(report,block)).filter(Boolean).join('\n\n');
}
export function applyReportEvent(current, event) {
  if (event.type === 'report_layout') {
    if (event.schema_version !== 1) return current;
    return {...(current || emptyReport()),outline:blocks(event.outline,true),artifacts:registry(event.artifacts),datasets:registry(event.datasets),facts:registry(event.facts)};
  }
  if (event.type === 'block_start' || event.type === 'block') {
    const block = blocks([event.block])[0];
    if (!block) return current;
    const report = current || emptyReport();
    if (report.blocks.length >= 128 || report.blocks.some(item => item.id === block.id)) return report;
    return {...report,blocks:[...report.blocks,block]};
  }
  if (event.type === 'block_delta' && current && validId(event.block_id) && typeof event.delta === 'string') {
    return {...current,blocks:current.blocks.map(block => block.id === event.block_id && block.type === 'markdown' ? {...block,content:block.content + event.delta,...(Array.isArray(block.parts) || Array.isArray(event.parts) ? {
      parts:[...(block.parts || [{type:'text',text:block.content}]),...(Array.isArray(event.parts) ? parts(event.parts) : [{type:'text',text:event.delta}])]
    } : {})} : block)};
  }
  return current;
}

const views = new WeakMap();
const stableJSON = value => JSON.stringify(value,(_,item) => object(item) ? Object.fromEntries(Object.entries(item).sort(([a],[b]) => a.localeCompare(b))) : item);
function element(doc, tag, text, className) {
  const node = doc.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
const own = (registry,id) => validId(id) && Object.hasOwn(registry,id) ? registry[id] : null;
function blockText(report, block, markdown = false) {
  if (!Array.isArray(block.parts)) return block.content;
  return block.parts.map(part => {
    if (part.type === 'text') return part.text;
    const fact = own(report.facts || {},part.fact_id);
    const value = typeof fact?.value === 'string' ? fact.value : '[数据暂不可用]';
    // Entity-encode punctuation before Markdown tokenization so source labels are literal.
    return markdown ? value.replace(/[^\p{L}\p{N}\s]/gu,char => `&#${char.codePointAt(0)};`).replace(/[\r\n]/g,' ') : value;
  }).join('');
}
function chartResources(report, block) {
  const artifact = own(report.artifacts,block.artifact_id);
  return {artifact,dataset:artifact ? own(report.datasets,artifact.dataset_id) : null};
}
function chartFallback(root, artifact, rows) {
  const doc = root.ownerDocument;
  if (typeof artifact?.title === 'string') root.append(element(doc,'h4',artifact.title));
  root.append(element(doc,'p','图表暂时无法显示。', 'review-chart-note'));
  if (!rows.length) return;
  const table = element(doc,'table'), head = element(doc,'thead'), header = element(doc,'tr');
  for (const label of ['记录 / 部位','记录时间',typeof artifact?.unit === 'string' ? `数值（${artifact.unit}）` : '数值']) {
    const cell = element(doc,'th',label); cell.setAttribute('scope','col'); header.append(cell);
  }
  head.append(header); table.append(head);
  const body = element(doc,'tbody');
  for (const row of rows.slice(0,200)) {
    if (!object(row)) continue;
    const line = element(doc,'tr');
    for (const key of ['label','at','value']) line.append(element(doc,'td',['string','number'].includes(typeof row[key]) ? String(row[key]) : '—'));
    body.append(line);
  }
  table.append(body);
  const scroll = element(doc,'div',undefined,'review-chart-table-scroll'); scroll.append(table); root.append(scroll);
  if (rows.length > 200 || artifact?.truncated) root.append(element(doc,'p','仅展示部分记录；图表不代表完整训练历史。','review-chart-note'));
}
function renderChart(root, resources) {
  const {artifact,dataset} = resources;
  const rows = Array.isArray(dataset?.rows) ? dataset.rows : [];
  const supported = artifact?.format === 'chart-spec/v1' && ['timeline','columns','bars'].includes(artifact.chart_type)
    && artifact.encoding?.label === 'label' && artifact.encoding?.value === 'value'
    && (artifact.chart_type !== 'timeline' || artifact.encoding?.at === 'at');
  const valid = rows.length && rows.every(row => object(row) && typeof row.label === 'string'
    && typeof row.value === 'number' && Number.isFinite(row.value) && row.value >= 0
    && (artifact?.chart_type !== 'timeline' || (typeof row.at === 'string' && Number.isFinite(Date.parse(row.at)))));
  if (!supported || !valid) { chartFallback(root,artifact,rows); return; }
  renderReviewCharts(root,[{...artifact,kind:artifact.chart_type,points:rows}]);
}

// Reconcile block wrappers only. Text deltas never recreate adjacent chart controls.
export function renderContentBlocks(root, report) {
  let entries = views.get(root);
  if (!entries) { root.replaceChildren(); entries = new Map(); views.set(root,entries); }
  const ordered = orderedBlocks(report), active = new Set(ordered.map(block => block.id));
  for (const [id,entry] of entries) if (!active.has(id)) { entry.node.remove(); entries.delete(id); }
  for (const [index,block] of ordered.entries()) {
    let entry = entries.get(block.id);
    if (!entry) {
      entry = {node:element(root.ownerDocument,'section',undefined,'report-block'),signature:null};
      entries.set(block.id,entry);
    }
    const resources = block.type === 'chart_ref' ? chartResources(report,block) : null;
    const signature = stableJSON([block,resources,block.type === 'markdown' ? report.facts : null]);
    if (entry.signature !== signature) {
      if (block.type === 'markdown') renderMarkdown(entry.node,blockText(report,block,true));
      else { entry.node.replaceChildren(); renderChart(entry.node,resources); }
      entry.signature = signature;
    }
    if (root.children[index] !== entry.node) root.insertBefore(entry.node,root.children[index] || null);
  }
}
