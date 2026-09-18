import test from 'node:test';
import assert from 'node:assert/strict';
import {newTurn, applyEvent} from '../../src/fitness_bot/web/stream.mjs';

const outline = [{id:'intro',type:'markdown'}, {id:'chart',type:'chart_ref',artifact_id:'volume'}, {id:'ending',type:'markdown'}];
const resources = () => ({artifacts:{volume:{format:'chart-spec/v1',chart_type:'bars',title:'训练量',unit:'组',dataset_id:'sets',encoding:{label:'label',value:'value',at:'at'},truncated:false}},datasets:{sets:{rows:[{label:'背部',value:8}]}}});
const layout = () => ({type:'report_layout',schema_version:1,outline,...resources()});
const send = (turn, type, rest) => applyEvent(turn,{type,...rest});
const begin = (turn,id,content='') => send(turn,'block_start',{block:{id,type:'markdown',content}});
const delta = (turn,id,value) => send(turn,'block_delta',{block_id:id,delta:value});
const chartBlock = {id:'chart',type:'chart_ref',artifact_id:'volume'};
const finalResponse = () => ({schema_version:1,outline,...resources(),text:'开头\n\n结尾',blocks:[{id:'intro',type:'markdown',content:'开头'},chartBlock,{id:'ending',type:'markdown',content:'结尾'}],kind:'review',cards:[]});

test('block streaming composes markdown once and preserves partial state on error', () => {
  const turn = newTurn('分析');
  applyEvent(turn,layout());
  begin(turn,'intro'); delta(turn,'intro','开头');
  send(turn,'text_delta',{delta:'开头'});
  send(turn,'block_end',{block_id:'intro'});
  send(turn,'block',{block:chartBlock});
  begin(turn,'ending'); delta(turn,'ending','结尾');
  send(turn,'error',{message:'断开'});
  assert.equal(turn.text,'开头\n\n结尾');
  assert.equal(turn.report?.blocks.length,3);
  assert.equal(turn.status,'failed');
});

test('legacy text and authoritative legacy done remain supported', () => {
  const turn = newTurn('旧记录');
  send(turn,'text_delta',{delta:'旧文本'});
  assert.equal(turn.text,'旧文本');
  send(turn,'done',{response:{text:'最终文本',cards:[],kind:'chat'}});
  assert.equal(turn.text,'最终文本');
  assert.ok(!turn.report);
});

// Small DOM boundary rejects all HTML parsing, and tracks node identity/reparenting.
class Element {
  constructor(tag,doc) { this.tagName=tag; this.ownerDocument=doc; this.children=[]; this.attributes={}; this.listeners={}; this.text=''; this.parentNode=null; }
  append(...nodes) { for (const node of nodes) this.insertBefore(node,null); }
  insertBefore(node,next) { node.remove(); const index=next ? this.children.indexOf(next) : this.children.length; this.children.splice(index,0,node); node.parentNode=this; }
  remove() { if (this.parentNode) { const siblings=this.parentNode.children; siblings.splice(siblings.indexOf(this),1); this.parentNode=null; } }
  replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.text=''; this.append(...nodes); }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  set textContent(value) { this.replaceChildren(); this.text=String(value); }
  get textContent() { return this.text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML parsing is forbidden'); }
}
const doc={createElement:tag=>new Element(tag,doc),createElementNS:(_,tag)=>new Element(tag,doc),createTextNode:text=>{const node=new Element('#text',doc);node.textContent=text;return node;}};
const host = () => new Element('section',doc);
const descendants = (root,tag) => [root,...root.children.flatMap(child=>descendants(child,tag))].filter(node=>node.tagName===tag);

test('out-of-order arrivals render at their declared positions and retain chart focus and expanded table', async () => {
  const {renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('分析'), root=host();
  applyEvent(turn,layout());
  send(turn,'block',{block:chartBlock});
  renderContentBlocks(root,turn.report);
  const figure=descendants(root,'figure')[0], table=descendants(root,'details')[0];
  table.open=true;
  const mark=descendants(root,'g')[0]; doc.activeElement=mark;
  begin(turn,'ending','结尾'); renderContentBlocks(root,turn.report);
  begin(turn,'intro','开头'); renderContentBlocks(root,turn.report);
  assert.equal(root.children.length,3);
  assert.match(root.children[0].textContent,/开头/);
  assert.equal(descendants(root.children[1],'figure')[0],figure);
  assert.match(root.children[2].textContent,/结尾/);
  assert.equal(descendants(root,'details')[0],table);
  assert.equal(table.open,true);
  assert.equal(doc.activeElement,mark);
  delta(turn,'ending','补充'); renderContentBlocks(root,turn.report);
  assert.ok(descendants(root,'figure')[0] === figure);
});

test('done replaces provisional blocks and matches history rendering without duplicating charts', async () => {
  const {renderContentBlocks,reportFromResponse}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('分析'), live=host(), saved=host();
  applyEvent(turn,layout()); begin(turn,'intro','临时'); send(turn,'block',{block:chartBlock});
  renderContentBlocks(live,turn.report);
  const chart=descendants(live,'figure')[0];
  const response=finalResponse();
  response.review={charts:[{kind:'bars',points:[{label:'重复',value:8}]}]};
  send(turn,'done',{response});
  renderContentBlocks(live,turn.report);
  renderContentBlocks(saved,reportFromResponse(response));
  assert.equal(live.textContent,saved.textContent);
  assert.equal(descendants(live,'figure').length,1);
  assert.equal(descendants(live,'figure')[0],chart);
  assert.doesNotMatch(live.textContent,/临时|重复/);
});

test('malformed blocks and prototype registry references are harmless', async () => {
  const {renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('分析'), root=host();
  applyEvent(turn,layout());
  for (const block of [null,{}, {id:'__proto__',type:'markdown',content:'bad'}, {id:{},type:'markdown',content:'bad'}]) send(turn,'block',{block});
  begin(turn,'intro','有效'); begin(turn,'intro','重复');
  delta(turn,'unknown','bad'); delta(turn,'intro',{});
  send(turn,'block',{block:{id:'missing',type:'chart_ref',artifact_id:'constructor'}});
  renderContentBlocks(root,turn.report);
  assert.equal(turn.report.blocks.length,2);
  assert.equal(turn.text,'有效');
  assert.match(root.textContent,/图表.*(?:无法|不可|缺少)/);
  assert.doesNotMatch(root.textContent,/bad|重复/);
});

test('unknown charts use an exact safe table; absent and invalid resources show a placeholder', async () => {
  const {renderContentBlocks,reportFromResponse}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const response=finalResponse();
  response.artifacts.volume.chart_type='future';
  response.datasets.sets.rows=[{label:'<img src=x onerror=alert(1)>',value:-0.0001,at:'原始日期'}];
  const root=host();
  renderContentBlocks(root,reportFromResponse(response));
  assert.equal(descendants(root,'table').length,1);
  assert.equal(descendants(root,'img').length,0);
  assert.match(root.textContent,/-0\.0001/);
  assert.match(root.textContent,/原始日期/);
  response.artifacts.volume.dataset_id='missing';
  renderContentBlocks(root,reportFromResponse(response));
  assert.equal(descendants(root,'table').length,0);
  assert.match(root.textContent,/图表.*(?:无法|不可|缺少)/);
});

test('registry snapshots are immutable and late resources refresh charts only when their data changes', async () => {
  const {renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('分析'),root=host(),event=layout();
  applyEvent(turn,event); send(turn,'block',{block:chartBlock});
  const previous=turn.report;
  renderContentBlocks(root,turn.report);
  const figure=descendants(root,'figure')[0];
  event.datasets.sets.rows[0].value=99;
  assert.equal(previous.datasets.sets.rows[0].value,8);
  applyEvent(turn,layout()); renderContentBlocks(root,turn.report);
  assert.ok(descendants(root,'figure')[0] === figure);
  const updated=layout();updated.datasets.sets.rows[0].value=10;
  applyEvent(turn,updated);renderContentBlocks(root,turn.report);
  assert.equal(previous.datasets.sets.rows[0].value,8);
  assert.notEqual(descendants(root,'figure')[0],figure);
  assert.match(root.textContent,/10/);
});

test('equivalent registries with reordered JSON properties retain chart DOM', async () => {
  const {renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('分析'),root=host();
  applyEvent(turn,layout());send(turn,'block',{block:chartBlock});renderContentBlocks(root,turn.report);
  const figure=descendants(root,'figure')[0];
  const response=finalResponse();
  response.artifacts.volume=Object.fromEntries(Object.entries(response.artifacts.volume).reverse());
  response.datasets.sets.rows[0]={value:8,label:'背部'};
  send(turn,'done',{response});renderContentBlocks(root,turn.report);
  assert.ok(descendants(root,'figure')[0] === figure);
});

test('unbounded block events cannot create an unbounded rendered report', () => {
  const turn=newTurn('分析');
  for (let index=0;index<200;index++) begin(turn,`block-${index}`,'内容');
  assert.equal(turn.report.blocks.length,128);
});

test('abort after a chart preserves both text blocks and chart data', async () => {
  const {consumeNDJSON}=await import('../../src/fitness_bot/web/stream.mjs');
  const turn=newTurn('分析'); let reads=0;
  const events=[layout(),{type:'block_start',block:{id:'intro',type:'markdown',content:'已有内容'}},{type:'block',block:chartBlock}];
  const body=new ReadableStream({pull(controller) {
    if (!reads++) controller.enqueue(new TextEncoder().encode(events.map(event=>JSON.stringify(event)).join('\n')+'\n'));
    else controller.error(new DOMException('Aborted','AbortError'));
  }});
  await assert.rejects(consumeNDJSON(new Response(body,{headers:{'Content-Type':'application/x-ndjson'}}),event=>applyEvent(turn,event)),{name:'AbortError'});
  assert.equal(turn.text,'已有内容');
  assert.equal(turn.report.blocks.length,2);
  assert.equal(turn.report.datasets.sets.rows[0].value,8);
});

test('short fact IDs resolve identically in streamed blocks and stored history', async () => {
  const {reportFromResponse,reportText,renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const turn=newTurn('复盘');
  const facts={fact_a3f29c01:{value:'2 次训练',evidence_id:'recent.frequency'}};
  applyEvent(turn,{...layout(),facts});
  send(turn,'block_start',{block:{id:'intro',type:'markdown',content:'',parts:[]}});
  send(turn,'block_delta',{block_id:'intro',delta:'共有 2 次训练。',parts:[{type:'text',text:'共有 '},{type:'fact_ref',fact_id:'fact_a3f29c01'},{type:'text',text:'。'}]});
  send(turn,'block',{block:chartBlock});
  assert.equal(turn.text,'共有 2 次训练。');
  const saved=reportFromResponse(JSON.parse(JSON.stringify(turn.report)));
  assert.deepEqual(saved,turn.report);
  assert.equal(reportText(saved),turn.text);
  const root=host(); renderContentBlocks(root,saved);
  assert.match(root.textContent,/共有 2 次训练。/);
  assert.equal(descendants(root,'figure').length,1);
});

test('fact values are literal, unknown references are visible, and legacy IDs still render', async () => {
  const {reportFromResponse,reportText,renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const response=finalResponse();
  response.facts={fact_12345678:{value:'[动作](https://example.com) <img src=x> **name**'}};
  response.blocks[0].parts=[{type:'fact_ref',fact_id:'fact_12345678'},{type:'fact_ref',fact_id:'fact_deadbeef'}];
  const report=reportFromResponse(response), root=host(); renderContentBlocks(root,report);
  assert.match(root.textContent,/\[动作\]\(https:\/\/example.com\) <img src=x> \*\*name\*\*/);
  assert.match(reportText(report),/数据暂不可用/);
  assert.equal(descendants(root,'a').length,0);
  assert.equal(descendants(root,'img').length,0);
  assert.match(root.textContent,/结尾/);
});

test('backend short-ID fixture renders without leaking reference markers', async () => {
  const {readFile}=await import('node:fs/promises');
  const {reportFromResponse,reportText,renderContentBlocks}=await import('../../src/fitness_bot/web/content-blocks.mjs');
  const response=JSON.parse(await readFile(new URL('../fixtures/report_short_ids_v1.json',import.meta.url),'utf8'));
  const report=reportFromResponse(response), root=host();
  assert.equal(reportText(report),response.text);
  assert.ok(report.blocks.every(block=>/^block_[0-9a-f]{8}$/.test(block.id)));
  renderContentBlocks(root,report);
  assert.equal(descendants(root,'figure').length,2);
  assert.doesNotMatch(root.textContent,/fact_[0-9a-f]{8}|\{\{fact:|数据暂不可用/);
});
