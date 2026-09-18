export function attestationPayload(observation, fields) {
  return {expected_version:observation.attestation?.version || 0, source:observation.source,
    confirmed:fields.confirmed, load_convention:fields.convention, conditions:fields.conditions,
    effort:fields.scale ? {scale:fields.scale,value:Number(fields.value),timing:fields.timing,
      observed_at:fields.observed.endsWith('Z') ? fields.observed : new Date(fields.observed).toISOString(),familiar_with_scale:fields.familiar} : null};
}

export function evidenceUI({api, run, notice}) {
  const dialog = document.createElement('dialog'); dialog.className = 'training-dialog';
  dialog.id = 'evidence-dialog';
  dialog.innerHTML = `<form method="dialog"><button class="dialog-close" aria-label="关闭">×</button></form>
    <h2>确认训练记录</h2>
    <p class="panel-intro">选择已同步记录，确认当时如何记重及用力感受。每条记录单独确认，不会修改原训练。</p>
    <form id="evidence-form">
      <label>动作记录<select id="evidence-observation" required></select></label>
      <p id="evidence-details" class="panel-intro"></p>
      <label>负重口径<select id="evidence-convention" required>
        <option value="total">总重量</option><option value="per_hand">每只哑铃 / 壶铃重量</option>
        <option value="added">附加重量</option><option value="assistance">辅助重量</option><option value="bodyweight">自重 / 未加负重</option>
        <option value="machine_stack">同一器械标示重量</option><option value="machine_level">同一器械档位</option>
      </select></label>
      <label>当时的器械与动作条件<input id="evidence-conditions" maxlength="300" required placeholder="例如：家中哑铃、同一平凳、相同动作幅度"></label>
      <p class="panel-intro">只有器械、动作幅度与技术标准一致时，才使用相同的条件说明。不同单位不合并。</p>
      <div class="training-fields">
        <label>用力量表<select id="evidence-scale"><option value="">未记录</option><option value="RIR">RIR（还能做几次）</option><option value="RPE">RPE（主观用力）</option></select></label>
        <label>数值<input id="evidence-value" type="number" min="0" max="10" step="0.5"></label>
        <label>对应范围<select id="evidence-timing"><option value="last_working_set">此记录最后工作组</option><option value="session">整次训练</option></select></label>
        <label>观察时间<input id="evidence-observed" type="datetime-local" step="1"></label>
      </div>
      <label class="training-check"><input id="evidence-familiar" type="checkbox">我熟悉所选量表</label>
      <label class="training-check"><input id="evidence-confirmed" type="checkbox">我确认以上信息；取消勾选可撤回原确认</label>
      <p class="panel-intro">加量讨论需近期两次条件一致的记录及最后工作组余力反馈。满足条件也不代表必须加量。</p>
      <button id="evidence-save" type="submit" class="send">保存确认</button>
    </form>`;
  document.body.append(dialog);
  const $ = id => dialog.querySelector(`#evidence-${id}`);
  let observations = [], originalObserved = '', displayedObserved = '';
  function selected() { return observations.find(o => o.id === $('observation').value); }
  function populate() {
    const o = selected();
    $('save').disabled = !o;
    if (!o) { $('details').textContent = '所选范围内没有可确认的已同步动作记录。'; return; }
    const a = o.attestation;
    $('details').textContent = `${o.completed_sets} 组 · 合计 ${o.repetitions} 次 · ${o.load_value ?? '无负重值'} ${o.load_unit || ''} · 训练记录版本 ${o.source.version}`;
    $('convention').value = a?.load_convention || (o.load_type === 'machineLevel' ? 'machine_level' : ['bodyweight','none'].includes(o.load_type) ? 'bodyweight' : ['added','assistance'].includes(o.load_type) ? o.load_type : 'total');
    $('conditions').value = a?.conditions || '';
    $('scale').value = a?.effort?.scale || '';
    $('value').value = a?.effort?.value ?? '';
    $('timing').value = a?.effort?.timing || 'last_working_set';
    originalObserved = a?.effort?.observed_at || o.performed_at;
    const at = new Date(originalObserved);
    $('observed').value = new Date(at.getTime()-at.getTimezoneOffset()*60000).toISOString().slice(0,19);
    displayedObserved = $('observed').value;
    $('familiar').checked = a?.effort?.familiar_with_scale || false;
    $('confirmed').checked = a?.confirmed || false;
  }
  $('observation').addEventListener('change',populate);
  document.getElementById('open-evidence').addEventListener('click',() => run(async current => {
    const profile = await api('/v1/training/profile');
    const facts = await api(`/v1/training/review?time_zone=${encodeURIComponent(profile.profile.time_zone)}`);
    if (!current()) return;
    observations = facts.observations;
    $('observation').replaceChildren();
    for (const o of observations) {
      const option = document.createElement('option'); option.value = o.id;
      option.textContent = `${new Date(o.performed_at).toLocaleString('zh-CN')} · ${o.exercise_name} · ${o.load_value ?? '无负重'} ${o.load_unit || ''}${o.attestation?.confirmed ? ' · 已确认' : ''}`;
      $('observation').append(option);
    }
    populate(); dialog.showModal();
  }));
  $('form').addEventListener('submit',event => {
    event.preventDefault();
    void run(async current => {
      const o = selected(); if (!o) return;
      if ($('scale').value && (!$('value').value || !$('observed').value)) throw new Error('请填写用力数值与观察时间。');
      const payload = attestationPayload(o, {confirmed:$('confirmed').checked,convention:$('convention').value,
        conditions:$('conditions').value,scale:$('scale').value,value:$('value').value,timing:$('timing').value,
        observed:$('observed').value === displayedObserved ? originalObserved : $('observed').value,familiar:$('familiar').checked});
      const result = await api(`/v1/training/observations/${o.id}/attestation`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (!current()) return;
      o.attestation = result; dialog.close(); notice('记录确认已保存，下一次复盘与方案会使用更新后的依据。',true);
    });
  });
  return {reset() {observations = []; dialog.close(); $('form').reset(); $('observation').replaceChildren();}};
}
