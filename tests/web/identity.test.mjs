import test from 'node:test';
import assert from 'node:assert/strict';
import {IdentitySession} from '../../src/fitness_bot/web/identity.mjs';

test('identity headers are applied to every API, with optional bearer', async () => {
  const calls = [];
  const identity = new IdentitySession({fetcher:async function(path, options) {
    assert.equal(this, undefined);
    calls.push(options.headers); return new Response('{}');
  }});
  await identity.fetch('/v1/me');
  assert.equal(calls[0].get('X-User-Id'), 'local-user');
  assert.equal(calls[0].get('Authorization'), null);
  const oldKey = identity.conversationKey;
  identity.setIdentity('alice', 'synthetic-token');
  await identity.fetch('/v1/chat', {headers:{Accept:'application/x-ndjson'}});
  assert.equal(calls[1].get('Authorization'), 'Bearer synthetic-token');
  assert.equal(calls[1].get('X-User-Id'), 'alice');
  assert.equal(calls[1].get('Accept'), 'application/x-ndjson');
  assert.notEqual(identity.conversationKey, oldKey);
  assert.throws(() => identity.setIdentity('alice\n', ''), /用户/);
  await assert.rejects(identity.fetch('https://example.com/v1/me'), /地址/);
  await assert.rejects(identity.fetch('//example.com/v1/me'), /地址/);
});

test('switching identities revokes protected images and rejects stale downloads', async () => {
  let finish;
  const revoked = [];
  const identity = new IdentitySession({fetcher:async path => {
    if (path.endsWith('slow')) await new Promise(resolve => { finish = resolve; });
    return new Response('pixels');
  }, objectUrls:{createObjectURL:()=> 'blob:test', revokeObjectURL:value => revoked.push(value)}});
  assert.equal(await identity.imageUrl('/v1/images/one'), 'blob:test');
  const pending = identity.imageUrl('/v1/images/slow');
  identity.setIdentity('bob', '');
  finish();
  await assert.rejects(pending, {name:'AbortError'});
  assert.deepEqual(revoked, ['blob:test']);
});

test('server generated ID replaces legacy alias while retaining in-memory token', async () => {
  const id = 'a123456789ab4def8123456789abcdef';
  const calls = [];
  const identity = new IdentitySession({fetcher:async (path, options) => {
    calls.push(options.headers); return new Response('{}');
  }});
  identity.setIdentity('alice', 'synthetic-token');
  identity.adoptServerUser(id);
  await identity.fetch('/v1/me');
  assert.equal(identity.userId, id);
  assert.equal(calls[0].get('X-User-Id'), id);
  assert.equal(calls[0].get('Authorization'), 'Bearer synthetic-token');
  assert.throws(() => identity.adoptServerUser('b123456789ab4def8123456789abcdef'), /不匹配/);
  assert.throws(() => identity.adoptServerUser('alice'), /内部/);
});
