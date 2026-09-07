import test from 'node:test';
import assert from 'node:assert/strict';
import {validateImages,uploadImage} from '../../src/fitness_bot/web/images.mjs';

test('reject oversize, unsupported formats, and more than three images', () => {
  const png = {type:'image/png',size:100,name:'图.png'};
  assert.doesNotThrow(()=>validateImages([png],2));
  assert.throws(()=>validateImages([png,png],2),/3/);
  assert.throws(()=>validateImages([{...png,size:5*1024*1024+1}],0),/5 MiB/);
  assert.throws(()=>validateImages([{...png,type:'image/heic'}],0),/JPEG/);
});

test('upload sends actual file bytes and reports server errors',async()=>{
  const file = new Blob(['image bytes'],{type:'image/png'});
  const asset = await uploadImage(file,async(url,options)=>{
    assert.equal(url,'/v1/images');
    assert.equal(options.method,'POST');
    assert.equal(options.body,file);
    assert.equal(options.headers['Content-Type'],'image/png');
    return new Response(JSON.stringify({id:'a'.repeat(64),url:'/v1/images/a'}),{status:201});
  });
  assert.equal(asset.id,'a'.repeat(64));
  await assert.rejects(uploadImage(file,async()=>new Response('{"detail":"图片损坏"}',{status:422})),/图片损坏/);
});

test('clipboard screenshots become image uploads, without duplicates or text interception',async()=>{
  const {clipboardImages} = await import('../../src/fitness_bot/web/images.mjs');
  const png = new Blob(['pixels'],{type:'image/png'});
  const item = {kind:'file',type:'image/png',getAsFile:()=>png};
  assert.deepEqual(clipboardImages({items:[item],files:[png]}),[png]);
  assert.deepEqual(clipboardImages({items:[],files:[png]}),[png]);
  assert.deepEqual(clipboardImages({items:[{kind:'string',type:'text/plain'}],files:[]}),[]);
  assert.deepEqual(clipboardImages({items:[{...item,getAsFile:()=>null}],files:[]}),[]);
  assert.deepEqual(clipboardImages(null),[]);
});
