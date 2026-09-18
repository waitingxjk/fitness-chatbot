import { renderReviewCharts } from './review-charts.mjs';
import { evidenceUI } from './evidence.mjs';
import { mountSessionPlan } from './session-plan.mjs';
import { planningPreview } from './plan-preview.mjs';

export function profilePayload(fields, previous) {
  const profile = {...previous.profile, goal:fields.goal || null, experience:fields.experience || null,
    session_minutes:fields.minutes ? Number(fields.minutes) : null,
    weekly_sessions:fields.weekly ? Number(fields.weekly) : null,
    equipment:fields.equipment, priority_exercise_ids:fields.priorities, time_zone:fields.timeZone};
  if (fields.updateReadiness) profile.readiness = {observed_at:new Date().toISOString(),
    pain:fields.pain || 'unknown', fatigue:fields.fatigue || 'unknown', sleep:fields.sleep || 'unknown', notes:fields.notes || ''};
  return {expected_version:previous.version, profile};
}

export function doseLabel(item) { return `${item.sets} 组 × ${item.repetitions ? `${item.repetitions} 次` : `${item.duration_seconds} 秒`}`; }
export function evidenceSummary(evidence) {
  const value = evidence.value || {};
  if (evidence.metric === 'recorded_performance') return `${value.performed_at?.slice(0,10) || ''} · ${value.exercise_name} · ${value.completed_sets} 组 · 合计 ${value.repetitions || 0} 次${value.duration_seconds ? ` / ${value.duration_seconds} 秒` : ''} · ${value.load_value ?? '无负重值'} ${value.load_unit || ''}${value.load_convention === 'unknown' ? '（负重口径未确认）' : ''}`;
  if (evidence.metric === 'frequency' && value.selection_mode === 'recent_workouts') return `所选 ${value.completed_workouts} 次训练 · ${value.completed_sets} 个已完成组${value.training_dates?.length ? ` · ${value.training_dates[0]} 至 ${value.training_dates.at(-1)}` : ''}`;
  if (evidence.metric === 'frequency') return `${value.start_date} 至 ${value.end_date_exclusive}（截止日不含）· ${value.completed_workouts} 次 · ${value.completed_sets} 组 · 每周 ${value.workouts_per_week} 次`;
  if (evidence.metric === 'session_sequence') return `动作顺序${value.order_ambiguous ? '（先后未确认）' : ''}：${(value.records || []).map(r => r.exercise_name).join(value.order_ambiguous ? '、' : ' → ')}${value.records_truncated ? '（仅展示部分）' : ''}`;
  if (evidence.metric === 'session_timing') return `记录时长：${value.duration_seconds ?? '未知'} 秒；实际组间/动作间休息：未知，录入间隔不能作为休息时间。`;
  if (evidence.metric === 'training_spacing') return `相邻训练开始间隔：${(value.intervals || []).map(i => `${i.start_gap_hours} 小时`).join('、') || '暂无'}${value.truncated ? '（仅展示部分）' : ''}`;
  if (evidence.metric === 'confirmed_cycle') return value.confirmed_cycle ? `用户确认周期：${value.confirmed_cycle.phase} · ${value.confirmed_cycle.starts_on} 至 ${value.confirmed_cycle.ends_on}` : '训练周期：未知';
  if (evidence.metric !== 'primary_body_part_sets') return '暂无可展示的依据摘要';
  return Object.entries(value).map(([part,count]) => `${part}：${count} 组`).join('，') || '没有可用分布记录';
}

const statuses = {proposed:'待采用',accepted:'已采用',skipped:'已跳过',executed:'已关联实际训练'};
const $ = id => document.getElementById(id);
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = String(text); if (cls) e.className = cls; return e; }
function option(value, label) { const e = node('option',label); e.value = value; return e; }

export function trainingUI({api, identityVersion, busy, setBusy, notice, compose, onDetails, loadThumbnail}) {
  let saved = null, priorities = [], execution = null, generation = 0;
  const run = async (operation) => {
    if (busy()) return;
    const version = identityVersion();
    setBusy(true);
    try { await operation(() => version === identityVersion()); }
    catch (error) { if (version === identityVersion()) notice(error.message); }
    finally { setBusy(false); }
  };
  const confirmations = evidenceUI({api, run, notice});
  function prioritiesUI() {
    $('training-priorities').replaceChildren();
    for (const item of priorities) {
      const button = node('button',`${item.name} ×`,'secondary'); button.type = 'button';
      button.addEventListener('click',() => { priorities = priorities.filter(p => p.id !== item.id); prioritiesUI(); });
      $('training-priorities').append(button);
    }
  }
  async function open() {
    await run(async current => {
      const [profile, options] = await Promise.all([api('/v1/training/profile'),api('/v1/training/options')]);
      if (!current()) return;
      saved = profile;
      const p = profile.profile;
      $('training-goal').value = p.goal || '';
      $('training-experience').value = p.experience || '';
      $('training-minutes').value = p.session_minutes || '';
      $('training-weekly').value = p.weekly_sessions || '';
      $('training-timezone').value = p.time_zone === 'UTC' && profile.version === 0 ? Intl.DateTimeFormat().resolvedOptions().timeZone : p.time_zone;
      $('training-equipment').replaceChildren();
      for (const item of options.equipment) {
        const label = node('label',undefined,'training-check');
        const input = node('input'); input.type = 'checkbox'; input.value = item.code; input.checked = p.equipment.includes(item.code);
        label.append(input,document.createTextNode(item.label)); $('training-equipment').append(label);
      }
      priorities = await Promise.all(p.priority_exercise_ids.map(async id => {
        const exercise = await api(`/v1/exercises/${encodeURIComponent(id)}`);
        return {id,name:exercise.displayNameZhHans};
      }));
      if (!current()) return;
      prioritiesUI(); $('training-search').value = ''; $('training-matches').replaceChildren();
      $('training-update-readiness').checked = false;
      for (const name of ['pain','fatigue','sleep']) $(`training-${name}`).value = 'unknown';
      $('training-notes').value = '';
      $('training-saved').textContent = p.readiness ? `上次状态记录：${new Date(p.readiness.observed_at).toLocaleString('zh-CN')}。本次状态请重新确认。` : '尚未填写当天状态。';
      $('training-cycle').value = p.confirmed_cycle?.phase || '';
      $('training-cycle-start').value = p.confirmed_cycle?.starts_on || '';
      $('training-cycle-end').value = p.confirmed_cycle?.ends_on || '';
      $('training-cycle-reason').value = p.confirmed_cycle?.reason || '';
      $('training-dialog').showModal();
    });
  }
  $('open-training').addEventListener('click',open);
  $('training-find').addEventListener('click',() => run(async current => {
    const query = $('training-search').value.trim(); if (!query) return;
    const matches = await api(`/v1/exercises?q=${encodeURIComponent(query)}&limit=10`);
    if (!current()) return;
    $('training-matches').replaceChildren();
    if (!matches.length) $('training-matches').append(node('p','未找到动作，请换一个名称。'));
    for (const exercise of matches) {
      const button = node('button',exercise.displayNameZhHans,'secondary'); button.type = 'button';
      button.addEventListener('click',() => {
        if (priorities.length >= 5 || priorities.some(p => p.id === exercise.id)) return;
        priorities.push({id:exercise.id,name:exercise.displayNameZhHans}); prioritiesUI();
      });
      $('training-matches').append(button);
    }
  }));
  $('training-form').addEventListener('submit',event => {
    event.preventDefault();
    void run(async current => {
      if (!saved) return;
      const payload = profilePayload({goal:$('training-goal').value,experience:$('training-experience').value,
        minutes:$('training-minutes').value,weekly:$('training-weekly').value,
        equipment:[...$('training-equipment').querySelectorAll('input:checked')].map(i => i.value),
        priorities:priorities.map(p => p.id),timeZone:$('training-timezone').value,
        updateReadiness:$('training-update-readiness').checked,pain:$('training-pain').value,
        fatigue:$('training-fatigue').value,sleep:$('training-sleep').value,notes:$('training-notes').value}, saved);
      payload.profile.confirmed_cycle = $('training-cycle').value ? {phase:$('training-cycle').value,
        starts_on:$('training-cycle-start').value,ends_on:$('training-cycle-end').value,reason:$('training-cycle-reason').value} : null;
      saved = await api('/v1/training/profile',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (!current()) return;
      $('training-dialog').close(); notice('训练目标与状态已保存，下次对话会使用这些信息。',true);
    });
  });
  function evidence(root, values, label = '查看记录依据') {
    const detail = node('details',undefined,'training-evidence'); detail.append(node('summary',label));
    for (const item of values) {
      detail.append(node('p',evidenceSummary(item)));
      if (item.value?.source) detail.append(node('small',`来源：训练 ${item.value.source.workout_id} · 版本 ${item.value.source.version} · 动作记录 ${item.value.source.record_id}`));
    }
    root.append(detail);
  }
  async function openFeedback(snapshot, render) {
    await run(async current => {
      const workouts = await api('/v1/training/completed-workouts');
      if (!current()) return;
      execution = {snapshot,render,workouts,eventId:crypto.randomUUID()};
      $('feedback-title').textContent = snapshot.plan.theme;
      $('feedback-kind').replaceChildren(option('feedback','仅记录反馈'));
      if (snapshot.status === 'accepted') $('feedback-kind').append(option('executed','关联已完成训练'));
      if (['proposed','accepted'].includes(snapshot.status)) $('feedback-kind').append(option('skipped','跳过本次方案'));
      $('feedback-kind').value = snapshot.status === 'accepted' ? 'executed' : 'feedback';
      $('feedback-workout').replaceChildren(option('','选择已同步训练'));
      workouts.forEach((workout,index) => $('feedback-workout').append(option(String(index),`${workout.started_at.slice(0,10)} · ${workout.title || '未命名训练'}`)));
      $('feedback-reason').value = ''; $('feedback-scale').value = ''; $('feedback-value').value = '';
      $('feedback-familiar').checked = false;
      $('feedback-observed').value = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
      $('feedback-dialog').showModal();
    });
  }
  $('feedback-form').addEventListener('submit',event => {
    event.preventDefault();
    void run(async current => {
      if (!execution) return;
      const {snapshot,render,workouts,eventId} = execution;
      const payload = {event_id:eventId,expected_revision:snapshot.revision,kind:$('feedback-kind').value,reason:$('feedback-reason').value};
      if (payload.kind === 'skipped' && !payload.reason.trim()) throw new Error('请填写跳过原因。');
      if (payload.kind === 'executed') {
        if (!$('feedback-workout').value) throw new Error('请选择实际完成的训练。');
        const workout = workouts[Number($('feedback-workout').value)];
        payload.workout = {source_id:workout.source_id,workout_id:workout.workout_id};
      }
      if ($('feedback-scale').value) {
        if (!$('feedback-value').value || !$('feedback-observed').value) throw new Error('请填写用力程度及观察时间。');
        payload.effort = {scale:$('feedback-scale').value,value:Number($('feedback-value').value),timing:$('feedback-timing').value,
          observed_at:new Date($('feedback-observed').value).toISOString(),familiar_with_scale:$('feedback-familiar').checked};
      }
      const updated = await api(`/v1/training/plans/${snapshot.plan.id}/events`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (!current()) return;
      render(updated); $('feedback-dialog').close(); notice('执行反馈已保存。',true);
    });
  });
  function render(root, response) {
    root.replaceChildren();
    const preview=planningPreview(response);
    if(preview) {
      const box=node('section',undefined,'training-plan plan-preview');
      box.append(node('h3',preview.status==='blocked'?'暂不生成训练计划':'完善这次训练安排'));
      box.append(node('p',preview.next_question,'plan-rationale'));
      const actions=node('div',undefined,'training-actions');
      const setup=node('button','补充训练条件','secondary');setup.type='button';setup.addEventListener('click',open);actions.append(setup);
      if(preview.status!=='blocked') {
        const retry=node('button',preview.legacy?'重新生成动作卡':'更新动作候选','secondary');retry.type='button';
        retry.addEventListener('click',()=>compose('请把刚才的训练安排整理为按顺序排列的动作卡；条件不完整时先给待完善候选，已补齐时生成完整计划。',null));
        actions.append(retry);
      }
      box.append(actions);root.append(box);
    }
    if (response?.review) {
      if (!(response.schema_version === 1 && Array.isArray(response.blocks))) renderReviewCharts(root, response.review.charts);
      const cited = new Map();
      for (const finding of response.review.findings) {
        for (const item of finding.evidence || []) cited.set(item.id || JSON.stringify(item),item);
      }
      if (cited.size) evidence(root,[...cited.values()],'查看分析依据');
      if (!response.review.findings.length) evidence(root,[{metric:'frequency',value:response.review.facts.recent}]);
      if (response.review.facts.limitations?.length) {
        const notes = node('details',undefined,'training-evidence');
        notes.append(node('summary','数据范围与说明'));
        for (const limitation of response.review.facts.limitations) notes.append(node('p',limitation));
        root.append(notes);
      }
    }
    if (!response?.session_plan) return;
    const token = generation, version = identityVersion();
    mountSessionPlan(root,response.session_plan,{
      api,run,notice,compose,evidence,openFeedback,onDetails,loadThumbnail,
      current:()=>token===generation && version===identityVersion(),
    });
  }
  return {open,render,reset() { confirmations.reset(); generation++; saved = execution = null; priorities = []; $('training-dialog').close(); $('feedback-dialog').close(); $('training-form').reset(); $('feedback-form').reset(); }};
}
