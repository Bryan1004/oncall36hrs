import test from 'node:test';
import assert from 'node:assert/strict';
import {closePolicy} from './connection-policy.mjs';
const error = (code, message = 'Connection Failure') => ({output: {statusCode: code}, message});
const decide = (code, changes = {}) => closePolicy({error: error(code), authenticated: false, hadQr: true, ...changes});

test('every disconnect stops until manually reconnected', () => {
  for (const code of [408, 403, 405, 428, 429, 500, 503, 515]) {
    assert.deepEqual(decide(code, {authenticated: true}), {state: 'error'});
  }
  assert.deepEqual(closePolicy({error: undefined, authenticated: true, hadQr: false}), {state: 'error'});
});
test('logged out and expired QR retain their specific states', () => {
  assert.deepEqual(decide(408, {error: error(408, 'QR refs attempts ended')}), {state: 'qr_expired'});
  assert.deepEqual(decide(401), {state: 'logged_out'});
  assert.deepEqual(decide(408, {authenticated: true, error: error(408, 'QR refs attempts ended')}), {state: 'error'});
});
