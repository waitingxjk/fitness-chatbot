import test from 'node:test';
import assert from 'node:assert/strict';
import {markdownNodes,renderMarkdown} from '../../src/fitness_bot/web/markdown.mjs';

const all = nodes => nodes.flatMap(node => typeof node === 'string' ? [] : [node,...all(node.children)]);
const text = nodes => nodes.map(node => typeof node === 'string' ? node : text(node.children)).join('');

test('Chinese recommendation renders bold and nested ordered/bulleted lists',()=>{
  const nodes = markdownNodes('可以参考：\n\n1. **杠铃卧推**\n   - 器械：杠铃\n   - 适合：胸部训练\n2. **俯卧撑**\n\n这次**训练历史不足**。');
  assert.deepEqual(all(nodes).filter(n=>n.tag==='strong').map(n=>text(n.children)),['杠铃卧推','俯卧撑','训练历史不足']);
  assert.equal(all(nodes).filter(n=>n.tag==='ol').length,1);
  assert.equal(all(nodes).filter(n=>n.tag==='ul').length,1);
  assert.ok(!text(nodes).includes('**'));
});
test('headings, tables, quotes, strikethrough, code and task lists',()=>{
  const nodes = markdownNodes('# 标题\n\n> 提示\n\n~~旧~~与`<b>`\n\n```python\nprint("<img>")\n```\n\n| 动作 | 次数 |\n| --- | ---: |\n| 卧推 | 12 |\n\n- [x] 完成');
  const tags = all(nodes).map(n=>n.tag);
  for(const tag of ['h1','blockquote','del','code','pre','table','th','td','input']) assert.ok(tags.includes(tag),tag);
  assert.equal(all(nodes).find(n=>n.tag==='input').attrs.disabled,'');
  assert.ok(text(nodes).includes('print("<img>")'));
});
test('HTML remains text and dangerous links never become active elements',()=>{
  const nodes = markdownNodes('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert) [x](java&#x73;cript:alert) [x](data:text/html,bad)\n\n[ok](https://example.com/?a=1&amp;b=2)');
  const elements = all(nodes);
  assert.ok(!elements.some(n=>['script','img','iframe'].includes(n.tag)));
  assert.equal(elements.filter(n=>n.tag==='a').length,1);
  assert.equal(elements.find(n=>n.tag==='a').attrs.href,'https://example.com/?a=1&b=2');
  assert.ok(text(nodes).includes('<script>'));
});
test('every streaming prefix parses and the final result matches a history render',()=>{
  const message = '## 建议\n\n**卧推**和[动作](https://example.com)\n\n```js\nconst x = "<tag>";\n```\n\n| 组数 | 次数 |\n| --- | --- |\n| 3 | 12 |';
  let latest;
  for(let i=1;i<=message.length;i++) latest=markdownNodes(message.slice(0,i));
  assert.deepEqual(latest,markdownNodes(message));
});
test('DOM adapter only creates nodes and text, including hostile code samples',()=>{
  const document = {
    createElement(tag){return {tag,attrs:{},children:[],setAttribute(key,value){this.attrs[key]=value;},append(...children){this.children.push(...children);}};},
    createTextNode(value){return value;}
  };
  const target = {ownerDocument:document,replaceChildren(...children){this.children=children;}};
  renderMarkdown(target,'**粗体** `<img onerror=alert(1)>`');
  assert.equal(all(target.children).find(n=>n.tag==='strong').children[0],'粗体');
  assert.ok(text(target.children).includes('<img onerror=alert(1)>'));
});
