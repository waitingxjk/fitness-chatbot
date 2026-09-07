import { consumeNDJSON, newTurn, applyEvent } from './stream.mjs';
import { validateImages, uploadImage, clipboardImages } from './images.mjs';
import { renderMarkdown } from './markdown.mjs';

const $ = id => document.getElementById(id);
const defaultContext = {available_equipment:[], recent_exercises:[], active_workout:null};
const state = {id:crypto.randomUUID(), turns:[], selected:null, busy:false, loading:false,
  attachments:[], uploading:false, history:[], hasMore:false, historyLoading:false, generation:0, controller:null, health:null};
const names = {understanding:'正在理解请求', retrieving:'正在检索动作', responding:'正在生成回复'};
const statusNames = {pending:'生成中', generating:'服务端处理中', completed:'已完成', failed:'未完成', cancelled:'已停止'};
const nodes = new WeakMap();
let noticeTimer;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function notice(message, temporary = false) {
  clearTimeout(noticeTimer);
  $('notice').textContent = message;
  $('notice').hidden = !message;
  if (temporary) noticeTimer = setTimeout(() => notice(''), 7000);
}
function rememberConversation() {
  try {
    if (state.turns.some(turn => turn.historical || turn.accepted || turn.response)) localStorage.setItem('fitness-playground-conversation', state.id);
    else localStorage.removeItem('fitness-playground-conversation');
  } catch { /* private mode */ }
}
function busyUI() {
  const locked = state.busy || state.loading || state.uploading;
  for (const id of ['new-chat','refresh-history','more-history','context','reset-context','reconnect','attach-image','image-files']) $(id).disabled = locked;
  $('send').disabled = locked || !state.health?.chat_configured || state.attachments.some(item => !item.asset);
  $('attach-image').textContent = state.uploading ? '上传中…' : '＋ 图片';
  $('attachments').querySelectorAll('button').forEach(button => { button.disabled = locked; });
  $('send').hidden = state.busy;
  $('stop').hidden = !state.busy;
  $('message').disabled = state.loading;
  $('messages').setAttribute('aria-busy', String(locked));
  document.querySelectorAll('[data-prompt]').forEach(button => { button.disabled = locked; });
  $('history').querySelectorAll('button').forEach(button => { button.disabled = locked; });
}
async function api(path) {
  const response = await fetch(path, {headers:{Accept:'application/json'}, signal:AbortSignal.timeout(12000)});
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* no JSON error */ }
    throw new Error(`HTTP ${response.status} · ${typeof detail === 'string' ? detail : '请求失败'}`);
  }
  return response.json();
}
async function connect() {
  $('connection-label').textContent = '连接中';
  $('connection-dot').className = 'dot';
  try {
    state.health = await api('/health');
    $('connection-label').textContent = state.health.chat_configured ? '已连接' : '模型未配置';
    $('connection-dot').className = `dot ${state.health.chat_configured ? 'online' : 'offline'}`;
    $('health-details').textContent = `服务状态：${state.health.status}\n动作库：${state.health.catalog_count.toLocaleString()} 个动作\n版本：${state.health.catalog_version}\n聊天模型：${state.health.chat_configured ? '已配置' : '未配置'}\n历史记录：服务端数据库`;
    if (!state.health.chat_configured) notice('服务已连接，但聊天模型未配置。请在服务端设置 OPENAI_API_KEY 和 OPENAI_MODEL 后重启。');
    else notice('');
    return true;
  } catch (error) {
    state.health = null;
    $('connection-label').textContent = '连接失败';
    $('connection-dot').className = 'dot offline';
    $('health-details').textContent = error.message;
    notice('无法连接 chatbot。请确认服务正在运行，然后点击右上角连接状态重试。');
    return false;
  } finally { busyUI(); }
}
function dateLabel(value) {
  if (!value) return '';
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function renderHistory() {
  const root = $('history'); root.replaceChildren();
  if (!state.history.length) root.append(el('p','history-empty','还没有对话。发送第一条消息后，会自动保存在这里。'));
  for (const item of state.history) {
    const button = el('button', `history-item${item.id === state.id ? ' active' : ''}`);
    button.append(el('strong','',item.title || '未命名对话'),el('small','',dateLabel(item.updated_at)));
    if (item.id === state.id) button.setAttribute('aria-current', 'page');
    button.title = item.title || '未命名对话';
    button.disabled = state.busy || state.loading || state.uploading;
    button.addEventListener('click', () => loadConversation(item.id));
    root.append(button);
  }
  $('more-history').hidden = !state.hasMore;
}
async function refreshHistory(append = false) {
  if (state.historyLoading) return;
  state.historyLoading = true;
  $('refresh-history').disabled = true;
  $('more-history').disabled = true;
  try {
    const rows = await api(`/v1/conversations?limit=50&offset=${append ? state.history.length : 0}`);
    state.history = append ? [...new Map([...state.history,...rows].map(row => [row.id,row])).values()] : rows;
    state.hasMore = rows.length === 50;
    renderHistory();
  } catch (error) { notice(`历史对话加载失败：${error.message}`); }
  finally { state.historyLoading = false; busyUI(); }
}
function showSidebar(open) {
  $('sidebar').classList.toggle('open',open);
  $('backdrop').hidden = !open;
  $('toggle-sidebar').setAttribute('aria-expanded',String(open));
}
function showInspector(open) {
  $('inspector').hidden = !open;
  $('inspector').classList.toggle('open',open);
  $('toggle-inspector').setAttribute('aria-expanded',String(open));
}
function selectTab(name) {
  for (const tab of document.querySelectorAll('[data-tab]')) {
    const selected = tab.dataset.tab === name;
    tab.setAttribute('aria-selected',String(selected)); tab.tabIndex = selected ? 0 : -1;
    $(`panel-${tab.dataset.tab}`).hidden = !selected;
  }
}
function startNew() {
  if (state.busy || state.uploading) return;
  state.generation++;
  state.loading = false;
  clearAttachments();
  state.id = crypto.randomUUID(); state.turns = []; state.selected = null;
  $('conversation-title').textContent = '新对话';
  $('context').value = JSON.stringify(defaultContext,null,2);
  $('message').value = '';
  rememberConversation(); renderConversation(); renderHistory(); busyUI();
  showSidebar(false); $('message').focus(); notice('');
}
async function loadConversation(id) {
  if (state.busy || state.uploading) return false;
  const version = ++state.generation;
  state.loading = true; busyUI(); notice('正在加载历史对话…');
  try {
    const rows = []; let after = 0;
    while (true) {
      const page = await api(`/v1/conversations/${encodeURIComponent(id)}/turns?limit=100&after_id=${after}`);
      if (version !== state.generation) return false;
      rows.push(...page);
      if (page.length < 100) break;
      const next = page.at(-1).id;
      if (next <= after) throw new Error('历史分页未前进，请刷新重试。');
      after = next;
    }
    clearAttachments();
    state.id = id;
    state.turns = rows.map(row => ({...newTurn(row.user_text,row.request_id), text:row.assistant_text,
      status:row.status, error:row.error_code ? `上次请求未完成（${row.error_code}）` : '',
      cards:row.response?.cards ?? [], trace:row.response?.trace ?? [], kind:row.response?.kind ?? 'chat',
      images:row.images ?? [], response:row.response, historical:true, createdAt:row.created_at}));
    state.selected = state.turns.at(-1) ?? null;
    $('conversation-title').textContent = state.history.find(row => row.id === id)?.title || rows[0]?.user_text || '历史对话';
    $('context').value = JSON.stringify(rows.at(-1)?.context ?? defaultContext,null,2);
    $('message').value = '';
    rememberConversation(); renderConversation(); renderHistory(); showSidebar(false); notice('');
    return true;
  } catch (error) { if (version === state.generation) notice(`读取对话失败：${error.message}`); return false; }
  finally { if (version === state.generation) { state.loading = false; busyUI(); } }
}
function details(title, value, className = '') {
  const root = el('details',className);
  root.append(el('summary','',title),el('pre','',JSON.stringify(value,null,2)));
  return root;
}
function renderInspector() {
  const turn = state.selected;
  const openEvents = new Set([...$('events').querySelectorAll('details[open]')].map(node => node.dataset.key));
  const openSteps = new Set([...$('trace').querySelectorAll('details[open]')].map(node => node.dataset.key));
  $('trace').replaceChildren(); $('events').replaceChildren(); $('run-meta').replaceChildren();
  if (!turn) {
    $('run-summary').textContent = '发送消息后，处理过程将在这里出现。';
    $('event-count').textContent = '尚无事件'; return;
  }
  $('run-summary').textContent = turn.status === 'pending' ? names[turn.stage] || turn.stage :
    `${statusNames[turn.status] || turn.status}${turn.elapsed ? ` · ${(turn.elapsed/1000).toFixed(1)} 秒` : ''}`;
  if (!turn.trace.length && turn.historical) $('trace').append(el('p','panel-intro','这条历史记录没有保存处理过程。新对话完成后会保存各阶段摘要。'));
  for (const step of turn.trace) {
    const node = el('article','trace-step');
    const data = details('查看阶段数据',step.data);
    data.dataset.key = `${turn.requestId}:${step.stage}`;
    data.open = openSteps.has(data.dataset.key);
    node.append(el('h3','',step.title),el('p','',step.summary),data);
    $('trace').append(node);
  }
  const meta = [['会话 ID',state.id],['请求 ID',turn.requestId]];
  if (turn.response?.result_id) meta.push(['结果 ID',turn.response.result_id]);
  if (turn.firstTextMs !== undefined) meta.push(['首段文本',`${(turn.firstTextMs/1000).toFixed(2)} s`]);
  if (turn.response?.catalog_version) meta.push(['动作库版本',turn.response.catalog_version]);
  for (const [label,value] of meta) if (value) $('run-meta').append(el('dt','',label),el('dd','',value));
  $('event-count').textContent = turn.historical ? '历史响应快照 · 不重建原始事件' : `${turn.eventCount} 条事件${turn.eventCount > 500 ? ' · 显示最近 500 条' : ''}`;
  if (turn.historical && turn.response) $('events').append(details('已保存的 response',turn.response,'event'));
  else for (const event of turn.events) {
    const node = details(`${event.sequence ?? '—'} · ${event.type}`,event,'event');
    node.dataset.key = `${turn.requestId}:${event.sequence}`;
    node.open = openEvents.has(node.dataset.key);
    $('events').append(node);
  }
}
function showExercise(card) {
  const exercise = card.exercise;
  $('exercise-title').textContent = exercise.displayNameZhHans;
  const root = $('exercise-detail'); root.replaceChildren();
  const tags = el('div','tags');
  for (const value of [card.body_part_label,card.equipment_label,exercise.targetMuscleCode]) tags.append(el('span','tag',value));
  root.append(tags,el('p','detail-id',`ID ${exercise.id}`),el('p','dialog-copy',card.context));
  root.append(el('h3','context-label','动作说明'));
  const steps = el('ol','detail-steps');
  for (const step of exercise.instructionStepsZhHans ?? []) steps.append(el('li','',step));
  root.append(steps,el('p','detail-source',`原始名称：${exercise.sourceName}\n动作库版本：${exercise.catalogVersion}`));
  $('exercise-dialog').showModal();
}
function renderCards(root, turn) {
  root.replaceChildren();
  if (!turn.cards.length) return;
  root.append(el('div','cards-label',`${turn.kind === 'recommendation' ? '为你推荐' : '搜索结果'} · ${turn.cards.length} 个动作`));
  const grid = el('div','card-grid');
  for (const card of turn.cards) {
    const button = el('button','exercise-card');
    button.type = 'button';
    button.append(el('strong','',card.exercise.displayNameZhHans));
    const tags = el('div','tags');
    for (const value of [card.body_part_label,card.equipment_label]) tags.append(el('span','tag',value));
    const footer = el('div','card-footer');
    footer.append(el('span','',`ID ${card.exercise.id}`),el('span','','动作详情 ↗'));
    button.append(tags,el('p','',card.context),footer);
    button.addEventListener('click',() => showExercise(card));
    grid.append(button);
  }
  root.append(grid);
}
function createTurnNode(turn) {
  const article = el('article','turn');
  const user = el('div','user-message');
  if (turn.message) user.append(el('div','',turn.message));
  if (turn.images?.length) {
    const gallery = el('div','message-images');
    for (const [index, asset] of turn.images.entries()) {
      const button = el('button','message-image'); button.type = 'button';
      const image = el('img'); image.src = asset.url; image.alt = `用户上传的图片 ${index + 1}`;
      image.loading = 'lazy';
      button.append(image);
      button.addEventListener('click',() => previewImage(asset.url));
      gallery.append(button);
    }
    user.append(gallery);
  }
  article.append(user);
  const header = el('div','assistant-header');
  const status = el('span','turn-state');
  header.append(el('span','avatar','练'),el('strong','','练伴'),status);
  const text = el('div','assistant-text');
  const cards = el('div'); const error = el('div','turn-error');
  const actions = el('div','turn-actions');
  const inspect = el('button','','查看处理过程 ↗');
  inspect.addEventListener('click',() => {
    state.selected = turn; renderInspector(); showInspector(true); selectTab('process');
    for (const item of state.turns) nodes.get(item)?.inspect.classList.toggle('selected',item === turn);
  });
  const copy = el('button','','复制回复');
  copy.addEventListener('click',async () => {
    try { await navigator.clipboard.writeText(turn.text); notice('已复制回复',true); }
    catch { notice('浏览器不允许访问剪贴板，请选择回复文本进行复制。',true); }
  });
  const retry = el('button','','再次发送此问题');
  retry.addEventListener('click',() => { if (!state.busy && !state.loading && !state.uploading) { $('message').value = turn.message; restoreAttachments(turn.images ?? []); $('message').focus(); } });
  actions.append(inspect,copy,retry);
  article.append(header,text,cards,error,actions);
  nodes.set(turn,{article,status,text,cards,error,inspect,retry,copy,lastCards:null});
  updateTurn(turn); return article;
}
function updateTurn(turn) {
  const view = nodes.get(turn); if (!view) return;
  view.status.textContent = turn.status === 'pending' ? names[turn.stage] || '处理中' : statusNames[turn.status] || turn.status;
  const source = turn.text || (turn.status === 'pending' ? ' ' : turn.status === 'generating' ? '这条记录仍在服务端处理中，稍后重新选择此对话查看结果。' : '这次请求没有返回文本。');
  if (view.lastText !== source) { renderMarkdown(view.text,source); view.lastText = source; }
  view.text.classList.toggle('pending',turn.status === 'pending');
  view.error.textContent = turn.error; view.error.hidden = !turn.error;
  view.retry.hidden = !['failed','cancelled'].includes(turn.status);
  view.copy.disabled = !turn.text;
  view.inspect.classList.toggle('selected',turn === state.selected);
  if (view.lastCards !== turn.cards) { renderCards(view.cards,turn); view.lastCards = turn.cards; }
}
function renderConversation() {
  $('welcome').hidden = state.turns.length > 0;
  $('turns').replaceChildren(...state.turns.map(createTurnNode));
  renderInspector(); scrollBottom();
}
function nearBottom() { const node = $('messages'); return node.scrollHeight - node.scrollTop - node.clientHeight < 100; }
function scrollBottom() { $('messages').scrollTop = $('messages').scrollHeight; }
async function sendMessage() {
  if (state.busy || state.loading || state.uploading || !state.health?.chat_configured) return;
  const message = $('message').value.trim(); if (!message && !state.attachments.length) return;
  if (state.attachments.some(item => !item.asset)) { notice('请移除或重新上传失败的图片。'); return; }
  let context;
  try {
    context = JSON.parse($('context').value);
    if (!context || typeof context !== 'object' || Array.isArray(context)) throw new Error('请输入 JSON 对象');
  } catch (error) { notice(`训练上下文格式错误：${error.message}`); showInspector(true); selectTab('context'); $('context').focus(); return; }
  state.busy = true; notice('');
  const turn = newTurn(message,crypto.randomUUID());
  turn.images = state.attachments.map(item => item.asset);
  clearAttachments();
  state.turns.push(turn); state.selected = turn;
  state.controller = new AbortController();
  const startedAt = performance.now();
  $('message').value = '';
  if (state.turns.length === 1) $('conversation-title').textContent = message || '图片对话';
  $('welcome').hidden = true; $('turns').append(createTurnNode(turn));
  renderInspector(); busyUI(); scrollBottom();
  try {
    const response = await fetch('/v1/chat', {method:'POST', headers:{'Content-Type':'application/json',Accept:'application/x-ndjson'},
      body:JSON.stringify({message,images:turn.images.map(asset => asset.id),conversation_id:state.id,request_id:turn.requestId,context}),signal:state.controller.signal});
    turn.accepted = response.ok;
    await consumeNDJSON(response,event => {
      const follow = nearBottom();
      if (event.type === 'text_delta' && turn.firstTextMs === undefined) turn.firstTextMs = performance.now() - startedAt;
      applyEvent(turn,event);
      turn.elapsed = performance.now() - startedAt;
      updateTurn(turn);
      // Keep raw event details stable while streaming text unless that tab is visible.
      if (state.selected === turn && (event.type !== 'text_delta' || !$('panel-events').hidden)) renderInspector();
      if (follow) scrollBottom();
    });
  } catch (error) {
    turn.status = error.name === 'AbortError' ? 'cancelled' : 'failed';
    turn.error = error.name === 'AbortError' ? '已停止生成，已收到的文本保留在这里。' : error.message;
  } finally {
    turn.elapsed = performance.now() - startedAt;
    state.busy = false; state.controller = null;
    updateTurn(turn); if (state.selected === turn) renderInspector(); busyUI();
    if (turn.status !== 'completed' && !$('message').value) { $('message').value = message; restoreAttachments(turn.images); }
    rememberConversation(); await refreshHistory(); $('message').focus();
  }
}

function previewImage(url) {
  $('image-preview').src = url;
  $('image-dialog').showModal();
}
function clearAttachments() {
  for (const item of state.attachments) if (item.preview?.startsWith('blob:')) URL.revokeObjectURL(item.preview);
  state.attachments = [];
  renderAttachments();
}
function restoreAttachments(assets) {
  clearAttachments();
  state.attachments = assets.map((asset,index) => ({asset,preview:asset.url,name:`图片 ${index + 1}`,status:'ready'}));
  renderAttachments(); busyUI();
}
function renderAttachments() {
  const root = $('attachments'); root.replaceChildren(); root.hidden = !state.attachments.length;
  for (const item of state.attachments) {
    const node = el('div',`attachment ${item.status}`);
    const preview = el('button','attachment-preview'); preview.type = 'button';
    const image = el('img'); image.src = item.preview; image.alt = item.name;
    preview.append(image); preview.addEventListener('click',() => previewImage(item.preview));
    const label = el('span','attachment-name',item.name); label.title = item.name;
    const info = el('span','attachment-state',item.status === 'uploading' ? '上传中…' : item.status === 'error' ? item.error : '已上传');
    const remove = el('button','attachment-remove','×'); remove.type = 'button'; remove.setAttribute('aria-label',`移除 ${item.name}`);
    remove.addEventListener('click',() => {
      if (item.preview?.startsWith('blob:')) URL.revokeObjectURL(item.preview);
      state.attachments = state.attachments.filter(other => other !== item); renderAttachments(); busyUI();
    });
    node.append(preview,label,info,remove);
    if (item.status === 'error') {
      const retry = el('button','attachment-retry','重新上传'); retry.type = 'button';
      retry.addEventListener('click',async () => {
        if (state.busy || state.loading || state.uploading) return;
        state.uploading = true; await uploadAttachment(item); state.uploading = false; busyUI();
      });
      node.append(retry);
    }
    root.append(node);
  }
}
async function uploadAttachment(item) {
  item.status = 'uploading'; renderAttachments(); busyUI();
  try { item.asset = await uploadImage(item.file); item.status = 'ready'; }
  catch (error) { item.status = 'error'; item.error = error.name === 'TimeoutError' ? '上传超时，请重试' : error.message; notice(`图片上传失败：${item.error}`); }
  finally { renderAttachments(); busyUI(); }
}
$('attach-image').addEventListener('click',() => $('image-files').click());
async function addImages(files) {
  if (!files.length || state.busy || state.loading || state.uploading) return;
  try { validateImages(files,state.attachments.length); } catch (error) { notice(error.message); return; }
  const items = files.map(file => ({file,name:file.name,preview:URL.createObjectURL(file),status:'uploading'}));
  state.attachments.push(...items); state.uploading = true; notice('');
  await Promise.all(items.map(uploadAttachment));
  state.uploading = false; busyUI(); $('message').focus();
}
$('image-files').addEventListener('change',() => {
  const files = [...$('image-files').files]; $('image-files').value = '';
  void addImages(files);
});
$('message').addEventListener('paste',event => {
  const files = clipboardImages(event.clipboardData);
  if (!files.length) return; // Ordinary text paste keeps native selection/undo behavior.
  event.preventDefault();
  if (state.busy || state.loading || state.uploading) { notice('请等待当前请求完成后再粘贴图片。'); return; }
  const pastedText = event.clipboardData.getData('text/plain');
  if (pastedText) {
    const input = $('message');
    const available = Math.max(0,input.maxLength - input.value.length + input.selectionEnd - input.selectionStart);
    input.setRangeText(pastedText.slice(0,available),input.selectionStart,input.selectionEnd,'end');
    input.dispatchEvent(new Event('input',{bubbles:true}));
  }
  void addImages(files);
});

$('composer').addEventListener('submit',event => { event.preventDefault(); void sendMessage(); });
$('message').addEventListener('keydown',event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void sendMessage(); }
});
$('stop').addEventListener('click',() => state.controller?.abort());
$('new-chat').addEventListener('click',startNew);
$('refresh-history').addEventListener('click',() => refreshHistory());
$('more-history').addEventListener('click',() => refreshHistory(true));
$('connection').addEventListener('click',() => $('connection-dialog').showModal());
$('reconnect').addEventListener('click',async () => { if (await connect()) await refreshHistory(); });
$('base-url').value = location.origin;
$('toggle-sidebar').addEventListener('click',() => showSidebar(!$('sidebar').classList.contains('open')));
$('backdrop').addEventListener('click',() => showSidebar(false));
$('toggle-inspector').addEventListener('click',() => showInspector($('inspector').hidden || (!$('inspector').classList.contains('open') && innerWidth <= 1000)));
$('reset-context').addEventListener('click',() => { $('context').value = JSON.stringify(defaultContext,null,2); });
for (const button of document.querySelectorAll('[data-prompt]')) button.addEventListener('click',() => { $('message').value = button.dataset.prompt; $('message').focus(); });
for (const tab of document.querySelectorAll('[data-tab]')) {
  tab.addEventListener('click',() => selectTab(tab.dataset.tab));
  tab.addEventListener('keydown',event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll('[data-tab]')];
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[index].dataset.tab); tabs[index].focus();
  });
}
document.addEventListener('keydown',event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) { event.preventDefault(); startNew(); }
  if (event.key === 'Escape') showSidebar(false);
});
if (innerWidth <= 1000) showInspector(false);
await connect();
await refreshHistory();
let saved;
try { saved = localStorage.getItem('fitness-playground-conversation'); } catch { /* private mode */ }
if (saved && /^[0-9a-f-]{36}$/i.test(saved)) await loadConversation(saved);

// Optional browser-native agent access shares the visible UI actions. No LLM call tools.
if (document.modelContext?.registerTool) {
  const lifecycle = new AbortController();
  window.addEventListener('pagehide',() => lifecycle.abort(),{once:true});
  const emptyInput = {type:'object',properties:{},additionalProperties:false};
  const validate = input => {
    if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).length) throw new Error('Expected an empty object');
  };
  const tools = [
    {name:'get_playground_state',title:'查看测试台状态',description:'Read connection status and the selected conversation ID without exposing chat content.',
      inputSchema:emptyInput,annotations:{readOnlyHint:true,untrustedContentHint:false},
      execute(input) { validate(input); return {connected:Boolean(state.health),chatConfigured:Boolean(state.health?.chat_configured),conversationId:state.id,turnCount:state.turns.length,busy:state.busy,uploading:state.uploading,loading:state.loading}; }},
    {name:'start_new_conversation',title:'打开新对话',description:'Open an empty conversation draft in the visible UI. Does not send a message or create a database record.',
      inputSchema:emptyInput,annotations:{readOnlyHint:false,untrustedContentHint:false},
      execute(input) { validate(input); if (state.busy || state.loading || state.uploading) throw new Error('A request is in progress'); startNew(); return {conversationId:state.id,status:'draft'}; }},
  ];
  for (const tool of tools) {
    try { Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(() => {}); }
    catch { /* Unsupported browser implementations do not affect the playground. */ }
  }
}
