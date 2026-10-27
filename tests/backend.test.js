import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {Backend} from '../lib/backend.js';

// These tests run a real Python child and loopback server, but never contact Hotstar.
test('managed relay starts once, protects its API and reports validation errors', async t => {
  const backend = new Backend();
  t.after(() => backend.stop());
  await Promise.all([backend.start(), backend.start()]);
  assert.match(backend.baseUrl, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.equal((await backend.request('/api/health')).status, 'ok');
  const unauthenticated = await fetch(backend.baseUrl + '/api/health');
  assert.equal(unauthenticated.status, 403);
  await assert.rejects(backend.request('/api/login/send_otp', {phone: 'invalid'}), /10-digit/);
  await assert.rejects(backend.resolve('https://example.test/in/shows/title/123456789/live'), /valid/);
  const child = backend.child;
  const closed = once(child, 'close');
  backend.stop();
  await closed;
  assert.equal(backend.child, null);
});

test('stopping during Python discovery cannot leave a service running', async () => {
  const backend = new Backend();
  const starting = backend.start();
  backend.stop();
  await assert.rejects(starting, /cancelled/);
  assert.equal(backend.child, null);
  assert.equal(backend.baseUrl, null);
});
