import test from 'node:test';
import assert from 'node:assert/strict';
import { consumeNDJSON, newTurn, applyEvent } from '../../src/fitness_bot/web/stream.mjs';

function response(text, size = 1) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({start(controller) {
    for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
    controller.close();
  }}), {headers: {'Content-Type': 'application/x-ndjson'}});
}

test('UTF-8 split bytes, CRLF, unknown events and unterminated final line', async () => {
  const events = [];
  await consumeNDJSON(response('\r\n{"type":"text_delta","delta":"哑铃💪"}\r\n{"type":"future"}\n{"type":"done","response":{"text":"完成"}}'), e => events.push(e));
  assert.deepEqual(events.map(e => e.type), ['text_delta', 'future', 'done']);
  assert.equal(events[0].delta, '哑铃💪');
});
test('EOF without terminal and malformed event fail explicitly', async () => {
  await assert.rejects(consumeNDJSON(response('{"type":"status"}\n'), () => {}), /中断/);
  await assert.rejects(consumeNDJSON(response('not json\n'), () => {}), /JSON/);
});
test('HTTP errors, stream errors, incorrect content types', async () => {
  await assert.rejects(consumeNDJSON(new Response('{"detail":"busy"}', {status:409}), () => {}), /409.*busy/);
  await assert.rejects(consumeNDJSON(new Response('<html>ok</html>'), () => {}), /NDJSON/);
  const turn = newTurn('test');
  await consumeNDJSON(response('{"type":"error","code":"timeout","message":"超时"}\n'), e => applyEvent(turn,e));
  assert.equal(turn.status, 'failed');
  assert.equal(turn.error, '超时');
});
test('done is authoritative; replay renders cards and saved trace without deltas', () => {
  const turn = newTurn('test');
  applyEvent(turn, {type:'text_delta', delta:'partial'});
  applyEvent(turn, {type:'trace', step:{stage:'understanding'}});
  const result = {text:'complete', cards:[{exercise:{id:'a'}}], trace:[{stage:'responding'}], kind:'search'};
  applyEvent(turn, {type:'done', response:result, replayed:true});
  assert.equal(turn.text, 'complete');
  assert.deepEqual(turn.cards, result.cards);
  assert.deepEqual(turn.trace, result.trace);
  assert.equal(turn.status, 'completed');
  const replay = newTurn('test');
  applyEvent(replay, {type:'done', response:result});
  assert.deepEqual(replay.cards, result.cards);
});
test('events after terminal are ignored and reader is cancelled', async () => {
  const types = [];
  await consumeNDJSON(response('{"type":"done","response":{}}\n{"type":"text_delta","delta":"bad"}\n', 1000), e => types.push(e.type));
  assert.deepEqual(types, ['done']);
});

test('transport failure preserves partial text and stops reading', async () => {
  let reads = 0;
  const stream = new ReadableStream({pull(controller) {
    if (!reads++) controller.enqueue(new TextEncoder().encode('{"type":"text_delta","delta":"已收到"}\n'));
    else controller.error(new DOMException('Aborted', 'AbortError'));
  }});
  const turn = newTurn('test');
  await assert.rejects(consumeNDJSON(new Response(stream,{headers:{'Content-Type':'application/x-ndjson'}}),e => applyEvent(turn,e)), {name:'AbortError'});
  assert.equal(turn.text,'已收到');
});

test('raw event history is bounded while all text deltas are retained', () => {
  const turn = newTurn('test');
  for (let i = 1; i <= 600; i++) applyEvent(turn,{type:'text_delta',delta:'字',sequence:i});
  assert.equal(turn.text.length,600);
  assert.equal(turn.eventCount,600);
  assert.equal(turn.events.length,500);
  assert.equal(turn.events[0].sequence,101);
});
