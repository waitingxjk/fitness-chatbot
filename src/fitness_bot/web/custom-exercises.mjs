/** Owner-only editing of the current catalog record; saved chat snapshots stay intact. */
export function editedDefinition(original, changes) {
  const result={...structuredClone(original),...changes};
  result.inferred_fields=(original.inferred_fields || []).filter(field=>
    JSON.stringify(original[field])===JSON.stringify(result[field]));
  return result;
}
const types={strength:'力量',cardio:'有氧',mobility:'活动度',stretching:'拉伸',other:'其他'};
const records={weightedReps:'重量＋次数',bodyweightReps:'自重次数',duration:'时长',machineLevelReps:'档位＋次数'};
const fields={name:'名称',aliases:'别名',body_part_code:'主要部位',equipment_code:'器械',target_muscle_code:'主要肌群',secondary_muscle_codes:'辅助肌群',training_type:'训练类型',detail_type:'记录方式',instructions:'动作说明',default_weight_unit:'重量单位'};
const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};

export async function mountCustomEditor(root, exercise, {api,isCurrent,loadThumbnail}) {
  const section=node('section');section.className='custom-exercise-editor';
  root.append(section);
  const status=node('p','正在读取个人动作…');status.setAttribute('role','status');section.append(status);
  try {
    let [snapshot,options]=await Promise.all([
      api(`/v1/custom-exercises/${encodeURIComponent(exercise.id)}`), api('/v1/custom-exercises/options')]);
    if(!isCurrent())return;
    section.replaceChildren();
    section.append(node('h3','管理个人动作'));
    const preview=node('img');preview.className='custom-exercise-avatar';preview.alt=`${exercise.displayNameZhHans}的头像`;
    section.append(preview);loadThumbnail(preview,exercise.id);
    const inferred=node('p');inferred.className='dialog-copy';
    const describe=()=>{
      const names=(snapshot.definition.inferred_fields || []).map(key=>fields[key] || key);
      inferred.textContent=names.length?`由助手补全：${names.join('、')}。你可以修改这些信息。`:'以下为已保存的个人动作信息。';
    };
    describe();section.append(inferred);
    const form=node('form');const controls={};
    const input=(key,tag='input',choices=null)=>{
      const label=node('label',fields[key]);const control=node(tag);
      control.name=key;
      if(choices)for(const [value,title] of choices){const option=node('option',title);option.value=value;control.append(option);}
      const value=snapshot.definition[key];control.value=key==='instructions'?value.join('\n'):value;
      if(tag==='input'){control.required=true;control.maxLength=120;}
      label.append(control);form.append(label);controls[key]=control;
    };
    input('name');
    for(const [key,type] of [['body_part_code','bodyPart'],['equipment_code','equipment'],['target_muscle_code','muscle']]){
      input(key,'select',[['unknown','待补充'],...options.terms.filter(t=>t.type===type).map(t=>[t.code,t.displayNameZhHans])]);
    }
    input('training_type','select',Object.entries(types));
    input('instructions','textarea');controls.instructions.rows=4;
    form.append(node('p',`记录方式：${records[snapshot.definition.detail_type]}。修改记录方式需另建动作。`));
    const buttons=node('div');buttons.className='custom-exercise-actions';
    const save=node('button','保存修改');save.type='submit';
    const archive=node('button',snapshot.exercise.isActive?'归档动作':'已归档');archive.type='button';archive.disabled=!snapshot.exercise.isActive;
    buttons.append(save,archive);form.append(buttons,status);status.textContent='';section.append(form);
    let busy=false;
    async function mutate(archiving){
      if(busy||!isCurrent())return;busy=true;save.disabled=true;archive.disabled=true;
      try{
        const changes=Object.fromEntries(Object.entries(controls).map(([key,c])=>[key,key==='instructions'?c.value.split('\n').map(v=>v.trim()).filter(Boolean):c.value]));
        const path=`/v1/custom-exercises/${encodeURIComponent(exercise.id)}`;
        snapshot=archiving?await api(`${path}?version=${snapshot.version}`,{method:'DELETE'}):
          await api(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:snapshot.version,exercise:editedDefinition(snapshot.definition,changes)})});
        if(!isCurrent())return;
        status.textContent=archiving?'已归档，历史训练记录仍保留。':'已保存；后续搜索和训练使用更新后的信息。';
        archive.textContent=snapshot.exercise.isActive?'归档动作':'已归档';
        save.textContent=snapshot.exercise.isActive?'保存修改':'保存并恢复';
        describe();loadThumbnail(preview,exercise.id);
      }catch(error){if(isCurrent())status.textContent=error.message;}
      finally{busy=false;save.disabled=false;archive.disabled=!snapshot.exercise.isActive;}
    }
    form.addEventListener('submit',event=>{event.preventDefault();void mutate(false);});
    archive.addEventListener('click',()=>void mutate(true));
    if(!snapshot.exercise.isActive)save.textContent='保存并恢复';
  }catch(error){if(isCurrent())status.textContent=error.message;}
}

/** Draft metadata and catalog persistence use separate, versioned user actions. */
export class DraftSaver {
  constructor(draft,api){this.draft=draft;this.api=api;this.pending=null;}
  async save(){
    if(this.draft.saved_result)return this.draft.saved_result;
    if(this.pending)return this.pending;
    this.pending=this.api(`/v1/custom-exercises/drafts/${encodeURIComponent(this.draft.draft_id)}/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:this.draft.version || 1})})
      .then(result=>{this.draft.saved_result=result;return result;});
    try{return await this.pending;}finally{this.pending=null;}
  }
  async update(changes){
    if(this.pending||this.draft.saved_result)throw Error('当前草稿正在保存或已保存，请刷新后再编辑。');
    this.pending=this.api(`/v1/custom-exercises/drafts/${encodeURIComponent(this.draft.draft_id)}`,{
      method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({version:this.draft.version || 1,exercise:editedDefinition(this.draft.definition,changes)})
    }).then(result=>{Object.assign(this.draft,result);return result;});
    try{return await this.pending;}finally{this.pending=null;}
  }

}

export function renderCustomDraft(root, draft, handlers) {
  const {api,isCurrent,loadImage,onDetails}=handlers;
  root.replaceChildren();
  if(draft?.status!=='draft')return;
  const section=node('section');section.className='custom-exercise-draft';
  const header=node('div');header.className='custom-draft-heading';
  const img=node('img');img.className='custom-exercise-avatar';img.alt=`${draft.definition.name}的头像`;
  loadImage(img,draft.avatar.url);
  const title=node('div');title.append(node('h3',draft.definition.name));
  const badge=node('p');title.append(badge);header.append(img,title);section.append(header);
  const previewBody=node('div');section.append(previewBody);
  const meta=node('dl');const spec=draft.definition,labels=draft.labels || {};
  for(const [label,value] of [['别名',spec.aliases?.join('、')],['训练类型',types[spec.training_type]],
    ['主要部位',labels.body_part],['主要肌群',labels.target_muscle],['辅助肌群',labels.secondary_muscles?.join('、')],
    ['器械',labels.equipment],['记录方式',records[spec.detail_type]],['重量单位',spec.default_weight_unit]]){
    if(value)meta.append(node('dt',label),node('dd',value));
  }
  previewBody.append(meta);
  if(spec.instructions?.length){const steps=node('ol');for(const step of spec.instructions)steps.append(node('li',step));previewBody.append(steps);}
  const inferred=(spec.inferred_fields || []).map(key=>fields[key] || key);
  if(inferred.length)previewBody.append(node('p',`助手补全：${inferred.join('、')}。可编辑草稿或继续对话调整。`));
  const actions=node('div');actions.className='custom-exercise-actions';
  const save=node('button','确认保存');save.type='button';
  const edit=node('button','编辑草稿');edit.type='button';
  const details=node('button','查看已保存动作');details.type='button';
  const message=node('p');message.setAttribute('role','status');
  const saver=new DraftSaver(draft,api);
  const refresh=()=>{const saved=draft.saved_result;save.disabled=!!saved;save.hidden=!!saved;edit.hidden=!!saved;details.hidden=!saved;badge.textContent=saved?'已保存到个人动作库':'动作草稿 · 尚未保存';};
  refresh();
  save.addEventListener('click',async()=>{
    if(!isCurrent()||saver.pending)return;
    save.disabled=true;edit.disabled=true;message.textContent='正在保存…';
    try{const result=await saver.save();if(!isCurrent())return;refresh();message.textContent=result.status==='existing'?'已关联已有动作，没有重复创建。':'保存成功，现在可以搜索并用于训练。';}
    catch(error){if(isCurrent()){save.disabled=false;edit.disabled=false;message.textContent=error.message;}}
  });
  details.addEventListener('click',()=>{const saved=draft.saved_result;if(isCurrent()&&saved)onDetails({exercise:saved.exercise,body_part_label:labels.body_part,equipment_label:labels.equipment,context:'已保存的个人动作',set_prescriptions:[]});});
  edit.addEventListener('click',async()=>{
    if(!isCurrent()||saver.pending)return;
    edit.disabled=true;save.disabled=true;message.textContent='正在准备编辑…';
    try{
      const options=await api('/v1/custom-exercises/options');
      if(!isCurrent())return;
      const form=draftEditorForm(spec,options);
      const editor=node('section');editor.className='custom-exercise-editor';
      editor.append(node('h3','编辑动作草稿'),node('p','应用修改只更新草稿；确认保存后才加入个人动作库。'),form.element);
      previewBody.hidden=true;actions.hidden=true;message.textContent='';section.append(editor);
      form.cancel.addEventListener('click',()=>{if(isCurrent())renderCustomDraft(root,draft,handlers);});
      form.element.addEventListener('submit',async event=>{
        event.preventDefault();if(!isCurrent()||saver.pending)return;
        form.apply.disabled=true;form.cancel.disabled=true;message.textContent='正在更新草稿…';
        try{
          await saver.update(form.read());
          if(isCurrent())renderCustomDraft(root,draft,handlers);
        }catch(error){if(isCurrent())message.textContent=error.message;}
        finally{form.apply.disabled=false;form.cancel.disabled=false;}
      });
      form.element.querySelector('input').focus();
    }catch(error){if(isCurrent()){edit.disabled=false;save.disabled=false;message.textContent=error.message;}}
  });
  actions.append(edit,save,details);section.append(actions,message);root.append(section);
}


function draftEditorForm(spec,options){
  const element=node('form'),controls={};
  const add=(key,tag='input',choices=null,multiple=false)=>{
    const label=node('label',fields[key]),control=node(tag);control.name=key;
    if(choices)for(const [value,title] of choices){const option=node('option',title);option.value=value;control.append(option);}
    control.multiple=multiple;
    if(multiple){for(const option of control.options)option.selected=(spec[key] || []).includes(option.value);control.size=5;}
    else control.value=Array.isArray(spec[key])?spec[key].join('\n'):spec[key] ?? '';
    if(key==='name'){control.required=true;control.maxLength=120;}
    if(tag==='textarea')control.rows=key==='instructions'?4:2;
    label.append(control);element.append(label);controls[key]=control;
  };
  const terms=type=>options.terms.filter(t=>t.type===type).map(t=>[t.code,t.displayNameZhHans]);
  add('name');add('aliases','textarea');
  element.append(node('small','别名每行填写一个；错别字不必保留为别名。'));
  add('training_type','select',Object.entries(types));
  for(const [key,type] of [['body_part_code','bodyPart'],['equipment_code','equipment'],['target_muscle_code','muscle']])
    add(key,'select',[['unknown','待补充'],...terms(type)]);
  add('secondary_muscle_codes','select',[['unknown','待补充'],...terms('muscle')],true);
  add('detail_type','select',Object.entries(records));
  add('default_weight_unit','select',[['','未指定'],['kg','kg'],['lb','lb']]);
  const updateUnit=()=>{controls.default_weight_unit.disabled=controls.detail_type.value!=='weightedReps';};
  controls.detail_type.addEventListener('change',updateUnit);updateUnit();
  add('instructions','textarea');element.append(node('small','动作说明每行填写一步。'));
  const actions=node('div');actions.className='custom-exercise-actions';
  const apply=node('button','应用修改'),cancel=node('button','取消编辑');apply.type='submit';cancel.type='button';
  actions.append(apply,cancel);element.append(actions);
  const read=()=>{
    const changes=Object.fromEntries(Object.entries(controls).map(([key,c])=>[key,
      key==='secondary_muscle_codes'?[...c.selectedOptions].map(o=>o.value):
      ['aliases','instructions'].includes(key)?c.value.split('\n').map(s=>s.trim()).filter(Boolean):c.value]));
    changes.default_weight_unit=changes.detail_type==='weightedReps'?(changes.default_weight_unit || null):null;
    return changes;
  };
  return {element,apply,cancel,read};
}
