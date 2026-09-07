import {marked} from './vendor/marked-16.4.2.mjs';

const element = (tag,children = [],attrs = {}) => ({tag,attrs,children});
function decode(text = '') {
  const named = {amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:'\u00a0'};
  return text.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi,(raw,value)=>{
    if(value[0]!=='#') return named[value.toLowerCase()] ?? raw;
    const code = value[1].toLowerCase()==='x' ? parseInt(value.slice(2),16) : Number(value.slice(1));
    return code > 0 && code <= 0x10ffff && !(code>=0xd800 && code<=0xdfff) ? String.fromCodePoint(code) : '\ufffd';
  });
}
function safeLink(href) {
  try {
    const url = new URL(decode(href));
    return ['http:','https:','mailto:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}
function link(href,children,title) {
  const url = safeLink(href);
  return url ? element('a',children,{href:url,target:'_blank',rel:'noopener noreferrer',...(title ? {title:decode(title)} : {})}) : element('span',children);
}
function tokensToNodes(tokens = [], depth = 0) {
  if(depth > 60) return [tokens.map(token=>token.raw ?? token.text ?? '').join('')];
  return tokens.flatMap(token => {
    const inline = () => token.tokens ? tokensToNodes(token.tokens,depth+1) : [decode(token.text)];
    switch(token.type) {
      case 'space': return [];
      case 'text': return inline();
      case 'escape': return [decode(token.text)];
      case 'html': return [token.raw]; // Treat model-provided HTML as literal text.
      case 'paragraph': return [element('p',inline())];
      case 'heading': return [element(`h${Math.max(1,Math.min(6,token.depth))}`,inline())];
      case 'strong': case 'em': case 'del': return [element(token.type,inline())];
      case 'br': case 'hr': return [element(token.type)];
      case 'codespan': return [element('code',[token.text])];
      case 'code': return [element('pre',[element('code',[token.text])])];
      case 'blockquote': return [element('blockquote',inline())];
      case 'link': return [link(token.href,inline(),token.title)];
      case 'image': return [link(token.href,[`图片：${decode(token.text) || '查看图片'}`],token.title)];
      case 'list': return [element(token.ordered ? 'ol' : 'ul',token.items.map(item => {
        const children = tokensToNodes(item.tokens,depth+1);
        if(item.task) children.unshift(element('input',[],{type:'checkbox',disabled:'',...(item.checked ? {checked:''} : {}),'aria-label':item.checked ? '已完成' : '未完成'}),' ');
        return element('li',children);
      }),token.ordered ? {start:String(token.start)} : {})];
      case 'table': {
        const row = (cells,header = false) => element('tr',cells.map(cell => element(header ? 'th' : 'td',tokensToNodes(cell.tokens,depth+1),['left','center','right'].includes(cell.align) ? {class:`align-${cell.align}`} : {})));
        return [element('div',[element('table',[element('thead',[row(token.header,true)]),element('tbody',token.rows.map(cells=>row(cells)))])],{class:'markdown-table'})];
      }
      default: return [token.raw ?? token.text ?? ''];
    }
  });
}
export function markdownNodes(source) {
  try { return tokensToNodes(marked.lexer(source,{gfm:true,breaks:true})); }
  catch { return [source]; }
}
export function renderMarkdown(target,source) {
  const document = target.ownerDocument;
  const build = node => {
    if(typeof node==='string') return document.createTextNode(node);
    const result = document.createElement(node.tag);
    for(const [key,value] of Object.entries(node.attrs)) result.setAttribute(key,value);
    result.append(...node.children.map(build));
    return result;
  };
  target.replaceChildren(...markdownNodes(source).map(build));
}
