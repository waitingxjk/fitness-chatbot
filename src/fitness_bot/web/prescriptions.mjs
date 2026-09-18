/** Shared exercise cards for an alternative and for a step in an ordered plan. */
const copy = value => structuredClone(value);
const conventions = {total:'总重量',per_hand:'每只手',added:'额外负重',assistance:'辅助重量',machine_stack:'配重片',machine_level:'器械档位',bodyweight:'自重',unknown:'口径未确认'};

export function prescriptionRows(item) {
  if (Array.isArray(item.set_prescriptions)) return copy(item.set_prescriptions);
  return Array.from({length:item.sets || 0}, () => ({kind:'working',repetitions:item.repetitions ?? null,
    duration_seconds:item.duration_seconds ?? null,rest_seconds:item.rest_seconds ?? 90,
    target_rir_min:item.target_rir_min ?? 2,target_rir_max:item.target_rir_max ?? 3,
    load:copy(item.load || {strategy:'trial',value:null,unit:null,convention:null})}));
}
export function prescriptionSummary(item) {
  const rows = prescriptionRows(item), work = rows.filter(r=>r.kind==='working'), warm = rows.length-work.length;
  if (!rows.length) return '查看动作详情';
  const parts = [`${work.length} 个正式组`];
  if (warm) parts.push(`${warm} 个热身组`);
  const dose = r => r.duration_seconds != null ? `${r.duration_seconds} 秒` : `${r.repetitions} 次`;
  const equal = rows.every(r => r.kind===rows[0].kind && dose(r)===dose(rows[0]) &&
    r.rest_seconds===rows[0].rest_seconds && r.target_rir_min===rows[0].target_rir_min &&
    r.target_rir_max===rows[0].target_rir_max && loadLabel(r.load)===loadLabel(rows[0].load));
  return equal ? `${parts.join(' · ')} × ${dose(rows[0])}` : `${parts.join(' · ')} · 每组设置不同`;
}
export function loadLabel(load = {}) {
  if (load.strategy === 'bodyweight') return '自重';
  if (load.strategy === 'none') return '—';
  if (load.value == null) return load.unit==='level' ? '档位待确认' : '待试重';
  const unit = load.unit==='level' ? '档' : load.unit || '';
  return `${load.value} ${unit}${load.convention && load.convention!=='machine_level' ? ` · ${conventions[load.convention] || '口径未确认'}` : ''}`;
}
function number(value, name, min, max, integer=true) {
  if (value === '' || value === null || typeof value === 'boolean' || !Number.isFinite(Number(value)) ||
      Number(value)<min || Number(value)>max || (integer && !Number.isInteger(Number(value)))) throw new Error(`${name}需为 ${min}–${max}${integer?' 的整数':' 范围内的数值'}。`);
  return Number(value);
}
export function cleanRows(values, type) {
  if (!values.length || values.length>12) throw new Error('每个动作需要 1–12 组。');
  const units=new Set(), scopes=new Set();
  const rows=values.map(raw=>{
    if (!['working','warmup'].includes(raw.kind)) throw new Error('请选择正式组或热身组。');
    const duration=type==='duration';
    if ((raw.duration_seconds != null)!==duration || (raw.repetitions != null)===duration) throw new Error('动作的次数／时长类型不匹配。');
    const r={kind:raw.kind,repetitions:duration?null:number(raw.repetitions,'次数',1,1000),
      duration_seconds:duration?number(raw.duration_seconds,'时长',1,3600):null,
      rest_seconds:number(raw.rest_seconds,'休息秒数',0,900),
      target_rir_min:number(raw.target_rir_min,'最低余力',2,6),target_rir_max:number(raw.target_rir_max,'最高余力',2,6)};
    if(r.target_rir_min>r.target_rir_max) throw new Error('最低余力不能大于最高余力。');
    const load=raw.load || {};
    if (['bodyweightReps','duration'].includes(type)) {
      if(load.value!=null || load.unit!=null) throw new Error('该动作不支持填写重量。');
      r.load={strategy:duration?'none':'bodyweight',value:null,unit:null,convention:duration?null:'bodyweight'};
    } else if(load.value==null || load.value==='') {
      r.load={strategy:'trial',value:null,unit:type==='machineLevelReps'?'level':(load.unit || 'kg'),convention:type==='machineLevelReps'?'machine_level':null};
    } else {
      const allowed=type==='machineLevelReps'?['level']:['kg','lb'];
      if(!allowed.includes(load.unit)) throw new Error('重量单位与动作记录类型不匹配。');
      if(!load.convention || load.convention==='unknown') throw new Error('请确认重量口径，例如总重量或每只手。');
      if (type==='machineLevelReps' && load.convention!=='machine_level') throw new Error('档位需要使用器械档位口径。');
      if (type==='weightedReps' && !['total','per_hand','added','assistance','machine_stack'].includes(load.convention)) throw new Error('请选择适合该动作的重量口径。');
      r.load={strategy:'user',value:number(load.value,'重量／档位',0.01,10000,type==='machineLevelReps'),unit:load.unit,convention:load.convention};
      units.add(load.unit); scopes.add(load.convention);
    }
    return r;
  });
  if(!rows.some(r=>r.kind==='working')) throw new Error('请至少保留一个正式组。');
  if(units.size>1 || scopes.size>1) throw new Error('同一动作各组的单位和重量口径必须一致。');
  return rows;
}
export function revisionItems(items) {
  return items.map(item=>({exercise_id:item.exercise.id,set_prescriptions:cleanRows(prescriptionRows(item),item.exercise.detailType)}));
}
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);if(cls)e.className=cls;return e;};
function button(text, handler, cls='secondary') {const b=node('button',text,cls);b.type='button';b.addEventListener('click',handler);return b;}

export function exercisePrescriptionCard(item,{order,onDetails,onChange,onReplace,onRemove,onMoveUp,onMoveDown,loadThumbnail,readOnly=false,pendingSetup=false}={}) {
  const exercise=item.exercise;
  let rows=prescriptionRows(item);
  const card=node('article',undefined,'prescription-card');
  const header=node('div',undefined,'prescription-header');
  if(order!=null) header.append(node('span',String(order).padStart(2,'0'),'prescription-order'));
  if(loadThumbnail) {
    const img=node('img');img.alt=exercise.displayNameZhHans;img.loading='lazy';img.className='prescription-thumbnail';
    header.append(img);loadThumbnail(img,exercise.id);
  }
  const identity=node('div',undefined,'prescription-identity');
  identity.append(node('strong',exercise.displayNameZhHans));
  const tags=node('div',undefined,'tags');
  for(const label of [item.body_part_label,item.equipment_label,exercise.sourceKind==='custom'?'自定义':null]) if(label) tags.append(node('span',label,'tag'));
  identity.append(tags);header.append(identity);
  if(onDetails) header.append(button('动作详情',()=>onDetails(item),'prescription-link'));
  card.append(header);
  if(item.purpose || item.context) card.append(node('p',item.purpose || item.context,'prescription-purpose'));
  const summary=node('p',pendingSetup?'组次、重量与休息待完善':prescriptionSummary({...item,set_prescriptions:rows}),'prescription-dose');card.append(summary);
  const errors=node('p','','prescription-error');errors.setAttribute('role','alert');errors.hidden=true;
  const changed=()=>{
    summary.textContent=prescriptionSummary({...item,set_prescriptions:rows});
    errors.hidden=true;onChange?.(copy(rows));
  };
  if(rows.length) {
    const details=node('details',undefined,'prescription-sets');
    details.append(node('summary',readOnly?'查看逐组设置':'查看／调整逐组设置'));
    const list=node('div',undefined,'set-editor-list');
    function field(label, value, update, {options,min=0,max=1000,step=1,placeholder}={}) {
      const wrap=node('label',undefined,'set-field');wrap.append(node('span',label));
      let input;
      if(options) {
        input=node('select');
        for(const [key,label] of options) {const o=node('option',label);o.value=key;input.append(o);}
      } else {input=node('input');input.type='number';input.min=min;input.max=max;input.step=step;if(placeholder)input.placeholder=placeholder;}
      input.value=value??'';input.disabled=readOnly;
      if(!readOnly) input.dataset.editControl='';
      input.addEventListener('change',()=>{update(options?input.value:input.value===''?null:Number(input.value));changed();});
      wrap.append(input);return wrap;
    }
    function paintRows() {
      list.replaceChildren();
      rows.forEach((r,index)=>{
        const group=node('div',undefined,'set-editor-row');
        const top=node('div',undefined,'set-editor-heading');
        top.append(node('strong',`第 ${index+1} 组`),node('span',loadLabel(r.load),'set-load-preview'));
        if(!readOnly && rows.length>1) {const remove=button('移除',()=>{rows.splice(index,1);changed();paintRows();},'prescription-link');remove.dataset.editControl='';top.append(remove);}
        group.append(top);
        const grid=node('div',undefined,'set-editor-fields');
        grid.append(field('组类型',r.kind,v=>r.kind=v,{options:[['working','正式组'],['warmup','热身组']]}));
        const duration=exercise.detailType==='duration';
        grid.append(field(duration?'时长（秒）':'次数',duration?r.duration_seconds:r.repetitions,v=>{r[duration?'duration_seconds':'repetitions']=v;},{min:1,max:duration?3600:1000}));
        if(['weightedReps','machineLevelReps'].includes(exercise.detailType)) {
          r.load ||= {strategy:'trial',value:null,unit:null};
          r.load.unit ||= exercise.detailType==='machineLevelReps'?'level':'kg';
          if(exercise.detailType==='machineLevelReps')r.load.convention='machine_level';
          grid.append(field(exercise.detailType==='machineLevelReps'?'档位':'重量',r.load.value,v=>{
            r.load.value=v;r.load.strategy=v==null?'trial':'user';delete r.load.observation_id;
            top.querySelector('.set-load-preview').textContent=loadLabel(r.load);
          },{min:0.01,max:10000,step:exercise.detailType==='machineLevelReps'?1:0.25,placeholder:'待试重'}));
          const machine=exercise.detailType==='machineLevelReps';
          grid.append(field('单位',r.load.unit || (machine?'level':'kg'),v=>r.load.unit=v,{options:machine?[['level','档']]:[['kg','kg'],['lb','lb']]}));
          grid.append(field('重量口径',r.load.convention || '',v=>r.load.convention=v||null,{options:machine?[['machine_level','器械档位']]:[['','请确认'],['total','总重量'],['per_hand','每只手'],['added','额外负重'],['assistance','辅助重量'],['machine_stack','配重片']]}));
        }
        grid.append(field('组后休息（秒）',r.rest_seconds,v=>r.rest_seconds=v,{max:900}));
        grid.append(field('最低余力（次）',r.target_rir_min,v=>r.target_rir_min=v,{min:2,max:6}));
        grid.append(field('最高余力（次）',r.target_rir_max,v=>r.target_rir_max=v,{min:2,max:6}));
        group.append(grid);list.append(group);
      });
      if(!readOnly && rows.length<12) {
        const add=button('＋ 添加一组',()=>{rows.push(copy(rows.at(-1)));changed();paintRows();});add.dataset.editControl='';list.append(add);
      }
    }
    paintRows();details.append(list,node('p','余力：完成本组后预计还能再做的次数。计划设置与实际完成记录分开保存。','prescription-help'));
    card.append(details);
  }
  card.append(errors);
  const actions=node('div',undefined,'prescription-actions');
  if(onReplace) {
    actions.append(button('换个动作',()=>onReplace(item)));
    for(const alternate of item.alternatives || []) actions.append(button(`换成${alternate.name}`,()=>onReplace(item,alternate)));
  }
  if(!readOnly && onRemove) actions.append(button('移出计划',onRemove));
  if(!readOnly && onMoveUp) actions.append(button('上移',onMoveUp));
  if(!readOnly && onMoveDown) actions.append(button('下移',onMoveDown));
  for(const b of actions.children) b.dataset.editControl='';
  if(actions.childElementCount)card.append(actions);
  return card;
}
