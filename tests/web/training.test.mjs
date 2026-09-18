import test from 'node:test';
import assert from 'node:assert/strict';
import {profilePayload, doseLabel, evidenceSummary} from '../../src/fitness_bot/web/training.mjs';

test('profile form preserves untouched cycle and does not fabricate readiness', () => {
  const previous = {version:3, profile:{confirmed_cycle:{phase:'deload'}, readiness:{observed_at:'2026-09-07T01:00:00Z'}, goal_notes:'目标说明'}};
  const result = profilePayload({goal:'strength', experience:'experienced', minutes:'45', weekly:'3', equipment:['dumbbell'], priorities:['0289'], timeZone:'Asia/Singapore', updateReadiness:false}, previous);
  assert.equal(result.expected_version, 3);
  assert.equal(result.profile.session_minutes, 45);
  assert.deepEqual(result.profile.confirmed_cycle, previous.profile.confirmed_cycle);
  assert.deepEqual(result.profile.readiness, previous.profile.readiness);
});

test('explicit readiness is dated and no numeric recovery score is invented', () => {
  const result = profilePayload({goal:'', experience:'', minutes:'', weekly:'', equipment:[], priorities:[], timeZone:'UTC', updateReadiness:true, pain:'none', fatigue:'high', sleep:'poor'}, {version:0,profile:{}});
  assert.equal(result.profile.goal, null);
  assert.equal(result.profile.session_minutes, null);
  assert.equal(result.profile.readiness.fatigue, 'high');
  assert.ok(!Number.isNaN(Date.parse(result.profile.readiness.observed_at)));
  assert.equal(result.profile.readiness.score, undefined);
});

test('dose and evidence summaries keep units and missing conventions visible', () => {
  assert.equal(doseLabel({sets:3,repetitions:8}), '3 组 × 8 次');
  assert.equal(doseLabel({sets:2,duration_seconds:30}), '2 组 × 30 秒');
  const text = evidenceSummary({metric:'recorded_performance',value:{exercise_name:'卧推',performed_at:'2026-09-07T01:00:00Z',completed_sets:3,repetitions:24,load_value:20,load_unit:'lb',load_convention:'unknown'}});
  assert.ok(text.includes('20 lb'));
  assert.ok(text.includes('口径未确认'));
});

test('observation confirmation binds source version and explicit effort without reusing session feedback', async () => {
  const {attestationPayload} = await import('../../src/fitness_bot/web/evidence.mjs');
  const observation = {source:{version:2,record_id:'r'},attestation:{version:3}};
  const result = attestationPayload(observation,{confirmed:true,convention:'per_hand',conditions:'same bench',scale:'RIR',value:'0',timing:'last_working_set',observed:'2026-09-07T01:00:00Z',familiar:true});
  assert.equal(result.expected_version,3); assert.equal(result.source.version,2);
  assert.equal(result.effort.value,0); assert.equal(result.effort.timing,'last_working_set');
  assert.equal(attestationPayload(observation,{confirmed:false,convention:'total',conditions:'x',scale:''}).effort,null);
});

test('review evidence presents sequence, real spacing and unknown rest clearly', () => {
  assert.match(evidenceSummary({metric:'session_sequence', value:{records:[{exercise_name:'卧推'},{exercise_name:'划船'}]}}), /卧推 → 划船/);
  assert.match(evidenceSummary({metric:'session_timing', value:{duration_seconds:3600}}), /休息.*未知/);
  assert.match(evidenceSummary({metric:'training_spacing', value:{intervals:[{start_gap_hours:12},{start_gap_hours:36}],truncated:true}}), /12 小时.*36 小时.*部分/);
  assert.match(evidenceSummary({metric:'confirmed_cycle', value:{status:'unknown'}}), /未知/);
  assert.match(evidenceSummary({metric:'future_metric', value:{records:[{}]}}), /暂无/);
});

test('count-based review evidence never invents a weekly rate', () => {
  const result = evidenceSummary({metric:'frequency',value:{selection_mode:'recent_workouts', completed_workouts:7, completed_sets:42, workouts_per_week:null, training_dates:['2025-01-01','2025-03-01']}});
  assert.match(result,/所选 7 次训练/);
  assert.match(result,/2025-01-01 至 2025-03-01/);
  assert.doesNotMatch(result,/每周|null|undefined/);
});
