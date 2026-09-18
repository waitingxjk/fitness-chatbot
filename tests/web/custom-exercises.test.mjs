import test from 'node:test';
import assert from 'node:assert/strict';
import {editedDefinition} from '../../src/fitness_bot/web/custom-exercises.mjs';

test('editing confirms only changed fields and preserves unknown metadata',()=>{
  const original={name:'墙天使',detail_type:'bodyweightReps',body_part_code:'back',training_type:'mobility',
    aliases:['Wall Angel'],inferred_fields:['body_part_code','training_type']};
  const next=editedDefinition(original,{name:'靠墙天使',body_part_code:'shoulders'});
  assert.equal(next.name,'靠墙天使');
  assert.deepEqual(next.inferred_fields,['training_type']);
  assert.equal(next.detail_type,'bodyweightReps');
  assert.deepEqual(next.aliases,['Wall Angel']);
  assert.equal(original.name,'墙天使');
});

test('draft saver has no write until explicit confirmation and retries one draft URL',async()=>{
  const {DraftSaver}=await import('../../src/fitness_bot/web/custom-exercises.mjs');
  const calls=[];let fail=true;
  const draft={status:'draft',draft_id:'draft-id',definition:{name:'墙天使'}};
  const saver=new DraftSaver(draft,async(path,options)=>{calls.push({path,options});if(fail){fail=false;throw Error('network');}return {status:'created',exercise:{id:'cx_12345678'}};});
  assert.equal(calls.length,0);
  await assert.rejects(saver.save(),/network/);
  assert.equal(draft.saved_result,undefined);
  await saver.save();await saver.save();
  assert.equal(calls.length,2);
  assert.equal(calls[0].path,calls[1].path);
  assert.equal(calls[0].options.method,'POST');
  assert.equal(draft.saved_result.exercise.id,'cx_12345678');
});

test('draft edits persist only preview and confirmation submits reviewed version',async()=>{
  const {DraftSaver}=await import('../../src/fitness_bot/web/custom-exercises.mjs');
  const calls=[];
  const draft={status:'draft',draft_id:'draft-id',version:1,definition:{name:'强天使',inferred_fields:['name']}};
  const saver=new DraftSaver(draft,async(path,options)=>{
    calls.push({path,...options});
    return options.method==='PUT'?{...draft,version:2,definition:{name:'墙天使',inferred_fields:[]}}:
      {status:'created',exercise:{id:'cx_12345678'}};
  });
  await saver.update({name:'墙天使'});
  assert.equal(calls.length,1);
  assert.equal(calls[0].method,'PUT');
  assert.equal(JSON.parse(calls[0].body).version,1);
  assert.equal(JSON.parse(calls[0].body).exercise.name,'墙天使');
  assert.equal(draft.version,2);
  assert.equal(draft.saved_result,undefined);
  await saver.save();
  assert.equal(JSON.parse(calls[1].body).version,2);
});

test('failed draft edit preserves original preview and version',async()=>{
  const {DraftSaver}=await import('../../src/fitness_bot/web/custom-exercises.mjs');
  const draft={draft_id:'draft-id',version:1,definition:{name:'墙天使'}};
  const saver=new DraftSaver(draft,async()=>{throw Error('conflict');});
  await assert.rejects(saver.update({name:'changed'}),/conflict/);
  assert.equal(draft.definition.name,'墙天使');
  assert.equal(draft.version,1);
});
