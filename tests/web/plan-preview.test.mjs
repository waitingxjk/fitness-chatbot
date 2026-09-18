import test from 'node:test';
import assert from 'node:assert/strict';
import {planningPreview, cardPresentation} from '../../src/fitness_bot/web/plan-preview.mjs';

test('incomplete session cards are ordered previews, never alternatives or executable sets', () => {
  const response={kind:'session_plan',session_plan:null,plan_preview:{status:'needs_input',theme:'上肢候选',next_question:'这次有多少时间？'}};
  assert.equal(planningPreview(response).next_question,'这次有多少时间？');
  const presentation=cardPresentation({kind:response.kind,response,cards:[{},{}]});
  assert.equal(presentation.preview,true);
  assert.match(presentation.label,/待完善/);
  assert.doesNotMatch(presentation.label,/搜索|任选/);
});

test('saved legacy deferred response explains absent cards and permits regeneration', () => {
  const preview=planningPreview({kind:'session_plan',trace:[{data:{plan_generation_deferred:true}}]});
  assert.equal(preview.legacy,true);
  assert.match(preview.next_question,/旧回复/);
  assert.equal(planningPreview({kind:'chat',trace:[]}),null);
  assert.equal(planningPreview({kind:'session_plan',session_plan:{items:[{}]}}),null);
});

test('search and single/alternative cards retain existing presentation', () => {
  assert.match(cardPresentation({kind:'search',cards:[{}]}).label,/搜索结果/);
  assert.equal(cardPresentation({kind:'recommendation',cards:[{}]}).label,'单个动作建议');
  assert.match(cardPresentation({kind:'recommendation',cards:[{},{}]}).label,/任选其一/);
});

test('custom exercise cards are labeled as personal catalog records',()=>{
  assert.equal(cardPresentation({kind:'custom_exercise',cards:[{}]}).label,'个人动作');
});
