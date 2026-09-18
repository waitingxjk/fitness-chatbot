import {exercisePrescriptionCard, prescriptionRows, revisionItems} from './prescriptions.mjs';

/** Keep mutation IDs through uncertain network outcomes; never accept an older draft. */
export class SessionPlanEditor {
  constructor(api,snapshot,newId=()=>crypto.randomUUID()) {
    this.api=api;this.newId=newId;this.operations=new Map();this.reset(snapshot);
  }
  reset(snapshot) {this.snapshot=snapshot;this.items=structuredClone(snapshot.plan.items);this.dirty=false;}
  changed() {this.dirty=true;}
  operationId(key) {if(!this.operations.has(key))this.operations.set(key,this.newId());return this.operations.get(key);}
  async save() {
    if(!this.dirty)return this.snapshot;
    const {plan,revision}=this.snapshot, items=revisionItems(this.items);
    const key=JSON.stringify({plan_id:plan.id,revision,items});
    const result=await this.api(`/v1/training/plans/${plan.id}/revision`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision_id:this.operationId(key),expected_revision:revision,items})});
    this.reset(result);return result;
  }
  async accept() {
    await this.save();
    const {plan,revision,status}=this.snapshot;
    if(status==='accepted')return this.snapshot;
    const result=await this.api(`/v1/training/plans/${plan.id}/events`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_id:this.operationId(`accept:${plan.id}:${revision}`),expected_revision:revision,kind:'accepted'})});
    this.reset(result);return result;
  }
}
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);if(cls)e.className=cls;return e;};
const statuses={proposed:'待确认',accepted:'已确认 · 待在 App 中开始',executed:'已关联完成的训练',skipped:'已跳过'};

export function mountSessionPlan(root,original,{api,run,current,notice,compose,evidence,openFeedback,onDetails,loadThumbnail}) {
  const box=node('section',undefined,'training-plan');root.append(box);
  let editor=null;
  const button=(label,callback,primary=false)=>{const b=node('button',label,primary?'send':'secondary');b.type='button';b.addEventListener('click',callback);return b;};
  function paint(snapshot) {
    if(!current() || !root.isConnected)return;
    editor ||= new SessionPlanEditor(api,snapshot);
    editor.reset(snapshot);
    box.replaceChildren();
    const plan=snapshot.plan;
    const eyebrow=node('div','整次训练计划 · 按顺序完成','plan-eyebrow');
    const header=node('div',undefined,'plan-title');
    header.append(node('h3',plan.theme),node('span',statuses[snapshot.status] || snapshot.status,'tag'));
    const total=node('p',`${plan.items.length} 个动作 · ${plan.working_sets} 个正式组 · 约 ${plan.estimated_minutes_min}–${plan.estimated_minutes_max} 分钟`,'plan-overview');
    const pending=node('p','','plan-edit-state');pending.hidden=true;
    box.append(eyebrow,header,total);
    if(plan.id!==original.id)box.append(node('p',`当前显示调整后的第 ${plan.version} 版，原建议保留在对话中。`,'prescription-help'));
    if(plan.rationale)box.append(node('p',plan.rationale,'plan-rationale'));
    if(plan.preparation)box.append(node('p',`准备 · ${plan.preparation}`,'plan-preparation'));
    const itemsRoot=node('div',undefined,'plan-exercises');box.append(itemsRoot,pending);
    const actions=node('div',undefined,'training-actions');
    const canEdit=['proposed','accepted'].includes(snapshot.status) && !snapshot.superseded_by && plan.items.length>0;
    const accept=button(snapshot.status==='accepted'?'已确认整份计划':'确认整份计划',()=>run(async valid=>{
      try {
        const result=await editor.accept();
        if(valid()&&current()) {paint(result);notice('整份计划已确认。可在同一账号的 App 中查看，点击开始后再记录。',true);}
      } catch(error) {
        // A revision may have committed successfully before acceptance failed.
        // Keep that version as the next retry target and explain the actual state.
        if(valid()&&current()&&!editor.dirty)paint(editor.snapshot);
        throw error;
      }
    }),true);
    const save=button('保存调整',()=>run(async valid=>{const result=await editor.save();if(valid()&&current()){paint(result);notice('调整已保存为新版本，确认后可用于训练。',true);}}));
    save.hidden=true;
    function changed() {
      editor.changed();pending.hidden=false;pending.textContent='有未保存的调整 · 确认时将先保存当前版本';
      total.textContent=`${editor.items.length} 个动作 · ${editor.items.reduce((n,i)=>n+prescriptionRows(i).filter(r=>r.kind==='working').length,0)} 个正式组 · 保存后重新计算用时`;
      save.hidden=false;accept.textContent='保存并确认整份计划';accept.hidden=false;
    }
    function renderItems() {
      itemsRoot.replaceChildren();
      editor.items.forEach((item,index)=>itemsRoot.append(exercisePrescriptionCard(item,{
        order:index+1,onDetails,loadThumbnail,readOnly:!canEdit,
        onChange:rows=>{item.set_prescriptions=rows;changed();},
        onReplace:canEdit?(_,alternate)=>compose(`请把「${plan.theme}」中的「${item.exercise.displayNameZhHans}」换成${alternate?`「${alternate.name}」`:'合适的替代动作'}，并重新计算组设置和时间。`,plan.id):null,
        onRemove:canEdit&&editor.items.length>1?()=>{editor.items.splice(index,1);changed();renderItems();}:null,
        onMoveUp:canEdit&&index>0?()=>{[editor.items[index-1],editor.items[index]]=[editor.items[index],editor.items[index-1]];changed();renderItems();}:null,
        onMoveDown:canEdit&&index<editor.items.length-1?()=>{[editor.items[index+1],editor.items[index]]=[editor.items[index],editor.items[index+1]];changed();renderItems();}:null,
      })));
    }
    renderItems();
    if(snapshot.status==='proposed' && !snapshot.superseded_by && plan.items.length)actions.append(accept);
    else if(snapshot.status==='accepted') {accept.hidden=true;actions.append(accept);}
    if(canEdit)actions.append(save);
    actions.append(button('让练伴重新调整',()=>compose(`请修改刚才的「${plan.theme}」方案，我的新条件是：`,plan.id)));
    actions.append(button('记录执行／跳过原因',()=>openFeedback(editor.snapshot,result=>paint(result))));
    box.append(actions,node('p',plan.items.length?'确认会保存计划。App 中点击「开始」后，才会创建本次训练的待记录组。':'本次建议休息，不创建待开始的训练。','prescription-help'));
    evidence(box,plan.evidence || []);
  }
  box.append(node('strong',original.theme),node('p','正在读取已保存的计划…'));
  async function load() {
    let snapshot=await api(`/v1/training/plans/${original.id}`);
    const visited=new Set([original.id]);
    for(let count=0;snapshot.superseded_by;count++) {
      if(count>=20 || visited.has(snapshot.superseded_by))throw new Error('计划版本链异常，请重新打开对话。');
      visited.add(snapshot.superseded_by);
      snapshot=await api(`/v1/training/plans/${snapshot.superseded_by}`);
    }
    paint(snapshot);
  }
  void load().catch(error=>{
    if(!current()||!root.isConnected)return;
    box.replaceChildren(node('strong',original.theme),node('p','读取计划状态失败，请重试。'));
    box.append(button('重试读取',()=>run(()=>load())));
    notice(error.message);
  });
}
