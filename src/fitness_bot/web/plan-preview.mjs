/** Distinguish incomplete session candidates from executable prescriptions. */
export function planningPreview(response) {
  if (!response || response.session_plan) return null;
  if (response.plan_preview) return response.plan_preview;
  if (response.kind==='session_plan' && response.trace?.some(step=>step.data?.plan_generation_deferred)) {
    return {status:'needs_input',legacy:true,theme:'训练安排待完善',
      next_question:'这条旧回复尚未生成动作卡，可以重新生成候选卡，或补充条件后生成完整计划。'};
  }
  return null;
}

export function cardPresentation(turn) {
  const preview=turn.kind==='session_plan' && !turn.response?.session_plan;
  return {preview,label:preview?'训练动作候选 · 按顺序查看 · 待完善'
    :turn.kind==='recommendation'
      ?turn.cards.length===1?'单个动作建议':`${turn.cards.length} 个备选动作 · 任选其一`
      :turn.kind==='custom_exercise'?'个人动作':`搜索结果 · ${turn.cards.length} 个动作`};
}
