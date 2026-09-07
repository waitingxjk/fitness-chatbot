/** Parse NDJSON at the byte boundary, including multi-byte Chinese characters. */
export async function consumeNDJSON(response, onEvent) {
  if (!response.ok) {
    let detail = '';
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body); } catch { /* status is sufficient */ }
    throw new Error(`HTTP ${response.status}${detail ? ` · ${detail}` : ''}`);
  }
  if (!response.headers.get('content-type')?.includes('application/x-ndjson')) throw new Error('接口没有返回 NDJSON 数据流，请检查连接地址。');
  if (!response.body) throw new Error('浏览器未提供可读取的数据流。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminal = false;
  const parse = line => {
    if (!line.trim() || terminal) return;
    let event;
    try { event = JSON.parse(line); } catch { throw new Error('无法解析流中的 JSON 事件。'); }
    if (!event || typeof event.type !== 'string') throw new Error('流事件缺少 type 字段。');
    onEvent(event);
    terminal = event.type === 'done' || event.type === 'error';
  };
  try {
    while (!terminal) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream:true});
      let index;
      while ((index = buffer.indexOf('\n')) >= 0 && !terminal) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        parse(line);
      }
      if (done) { parse(buffer); break; }
    }
    if (!terminal) throw new Error('连接已中断，尚未收到完成事件。已显示的内容可能不完整。');
  } finally {
    try { await reader.cancel(); } finally { reader.releaseLock(); }
  }
}

export function newTurn(message, requestId = null) {
  return {message, requestId, text:'', cards:[], trace:[], events:[], eventCount:0,
    status:'pending', stage:'understanding', kind:'chat', error:'', response:null};
}

export function applyEvent(turn, event) {
  turn.eventCount++;
  turn.events.push(event);
  if (turn.events.length > 500) turn.events.shift();
  switch (event.type) {
    case 'status': turn.stage = event.stage; break;
    case 'trace': turn.trace.push(event.step); break;
    case 'text_delta': turn.text += event.delta; break;
    case 'exercise_cards': turn.cards = event.cards; turn.kind = event.kind; break;
    case 'done':
      turn.response = event.response;
      turn.text = event.response.text ?? '';
      turn.cards = event.response.cards ?? [];
      turn.trace = event.response.trace ?? turn.trace;
      turn.kind = event.response.kind;
      turn.status = 'completed';
      break;
    case 'error': turn.status = 'failed'; turn.error = event.message ?? event.code; break;
    // Forward-compatible event types remain visible in the event inspector.
  }
  return turn;
}
