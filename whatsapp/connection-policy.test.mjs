import test from 'node:test';
import assert from 'node:assert/strict';
import {closePolicy, restartAfterAuth} from './connection-policy.mjs';
const error = (code, message = 'Connection Failure') => ({output: {statusCode: code}, message});
const decide = (code, changes = {}) => closePolicy({error: error(code), authenticated: false, hadQr: true, attempts: 0, ...changes});

test('post-scan restart works even before socket.user is available', () => {
  assert.deepEqual(decide(515), {state: 'reconnecting', delay: 5000});
});
test('only unpaired QR exhaustion expires the QR', () => {
  assert.deepEqual(decide(408, {error: error(408, 'QR refs attempts ended')}), {state: 'qr_expired'});
  assert.deepEqual(decide(408), {state: 'error'});
  assert.deepEqual(decide(408, {hadQr: false}), {state: 'error'});
  assert.equal(decide(408, {authenticated: true}).state, 'reconnecting');
  assert.equal(decide(408, {authenticated: true, error: error(408, 'QR refs attempts ended')}).state, 'reconnecting');
});
test('unpaired rejection is an error, not an expired QR; terminal states never retry', () => {
  assert.deepEqual(decide(405), {state: 'error'});
  assert.deepEqual(decide(401), {state: 'logged_out'});
  for (const code of [403, 440, 500, 411]) assert.deepEqual(decide(code, {authenticated: true}), {state: 'error'});
});
test('retries back off and stop, including repeated restartRequired', () => {
  for (const code of [515, 408, 428, 503]) {
    assert.equal(decide(code, {authenticated: true, attempts: 3}).delay, 40000);
    assert.equal(decide(code, {authenticated: true, attempts: 4}).delay, 60000);
    assert.deepEqual(decide(code, {authenticated: true, attempts: 5}), {state: 'error'});
  }
});
test('restart waits for saved credentials and respects intervening disconnect', async () => {
  for (const current of [true, false]) {
    let finish, calls = 0;
    const saved = new Promise(resolve => { finish = resolve; });
    const pending = restartAfterAuth(saved, () => current, async () => { calls++; });
    await Promise.resolve();
    assert.equal(calls, 0);
    finish();
    await pending;
    assert.equal(calls, current ? 1 : 0);
  }
});
test('failed credential persistence must not start a new socket', async () => {
  let calls = 0;
  await assert.rejects(restartAfterAuth(Promise.reject(new Error('write failed')), () => true, async () => { calls++; }));
  assert.equal(calls, 0);
});
