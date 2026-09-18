import test from 'node:test';
import assert from 'node:assert/strict';
import {prescriptionRows, prescriptionSummary, loadLabel, revisionItems, cleanRows} from '../../src/fitness_bot/web/prescriptions.mjs';

const row = (repetitions=8) => ({kind:'working',repetitions,duration_seconds:null,rest_seconds:90,target_rir_min:2,target_rir_max:3,load:{strategy:'trial',value:null,unit:'kg',convention:null}});
test('legacy snapshot expands independent rows without changing original', () => {
  const item = {sets:3,repetitions:8,load:{strategy:'trial',value:null}};
  const rows = prescriptionRows(item);
  assert.equal(rows.length,3); rows[0].load.value=10;
  assert.equal(rows[1].load.value,null); assert.equal(item.load.value,null);
});
test('explicit mixed rows remain authoritative and summarized honestly', () => {
  const item = {sets:99,repetitions:99,set_prescriptions:[{...row(12),kind:'warmup'},row(8),row(6)]};
  assert.equal(prescriptionRows(item).length,3);
  assert.equal(prescriptionSummary(item),'2 个正式组 · 1 个热身组 · 每组设置不同');
  assert.equal(prescriptionSummary({set_prescriptions:[row(),row()]}),'2 个正式组 × 8 次');
});
test('load label distinguishes trial, per hand, machine levels and duration', () => {
  assert.equal(loadLabel({strategy:'trial',value:null,unit:'kg'}),'待试重');
  assert.ok(loadLabel({strategy:'user',value:10,unit:'kg',convention:'per_hand'}).includes('每只手'));
  assert.equal(loadLabel({strategy:'none',value:null}),'—');
  assert.ok(loadLabel({strategy:'user',value:3,unit:'level',convention:'machine_level'}).includes('3 档'));
});
test('revision serializer preserves row order and user-entered values', () => {
  const items = [{exercise:{id:'0289',detailType:'weightedReps'},set_prescriptions:[row(8),row(6)]}];
  const result = revisionItems(items);
  assert.deepEqual(result[0].set_prescriptions.map(s=>s.repetitions),[8,6]);
  assert.equal(result[0].exercise_id,'0289');
});
test('editing rejects missing conventions, NaN, mixed units and incompatible dose', () => {
  const a = row(); a.load = {strategy:'user',value:10,unit:'kg',convention:null};
  assert.throws(()=>cleanRows([a],'weightedReps'),/口径/);
  a.load.convention='per_hand'; a.load.value=NaN;
  assert.throws(()=>cleanRows([a],'weightedReps'),/重量/);
  a.load.value=10;
  assert.throws(()=>cleanRows([a,{...row(),load:{...a.load,unit:'lb'}}],'weightedReps'),/单位/);
  assert.throws(()=>cleanRows([row()],'duration'),/时长/);
});

test('save retry reuses revision ID and acceptance targets the edited version', async () => {
  const {SessionPlanEditor} = await import('../../src/fitness_bot/web/session-plan.mjs');
  const calls=[];let fail=true, serial=0;
  const plan={id:'original',items:[{exercise:{id:'0289',detailType:'weightedReps'},set_prescriptions:[row()]}]};
  const editor=new SessionPlanEditor(async (path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body});
    if(path.endsWith('/revision')) {
      if(fail) {fail=false;throw new Error('network');}
      return {plan:{...plan,id:body.revision_id},status:'proposed',revision:0};
    }
    return {plan:{...plan,id:'id-1'},status:'accepted',revision:1};
  },{plan,status:'proposed',revision:0},()=>`id-${++serial}`);
  editor.items[0].set_prescriptions[0].repetitions=6;editor.changed();
  await assert.rejects(editor.accept(),/network/);
  assert.equal(editor.dirty,true);
  await editor.accept();
  assert.equal(calls[0].body.revision_id,calls[1].body.revision_id);
  assert.equal(calls[2].path,'/v1/training/plans/id-1/events');
  assert.equal(calls[1].body.items[0].set_prescriptions[0].repetitions,6);
  assert.equal(editor.snapshot.status,'accepted');
});
