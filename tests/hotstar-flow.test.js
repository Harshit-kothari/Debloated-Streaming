import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareHotstarPlayback, resumeHotstarPlayback} from '../lib/hotstar-flow.js';

const player = {key: 'mpv', label: 'MPV', bin: 'C:/Apps/mpv/mpv.exe', args: ['--cache=yes']};
const intent = {url: 'https://www.hotstar.com/in/shows/event/123456789/live', player, lang: 'hin'};

test('missing or expired account login pauses resolution and preserves the playback choice', async () => {
  for (const status of ['none', 'expired', 'unavailable']) {
    const calls = [];
    const backend = {
      request: async route => {calls.push(route); return {status};},
      resolve: async () => assert.fail('Do not resolve before completing login.'),
    };
    const result = await prepareHotstarPlayback(backend, intent);
    assert.equal(result.requiresLogin, true);
    assert.deepEqual(result.pending, intent);
    assert.deepEqual(calls, ['/api/token/status']);
  }
});

test('a valid saved login resolves immediately using the chosen URL and language', async () => {
  const calls = [];
  const backend = {
    request: async () => ({status: 'valid'}),
    resolve: async (...args) => {calls.push(args); return {url: 'http://127.0.0.1:12345/proxy/session/master.m3u8', shareId: 'session'};},
  };
  const result = await prepareHotstarPlayback(backend, intent);
  assert.equal(result.requiresLogin, false);
  assert.equal(result.player, player);
  assert.equal(result.shareId, 'session');
  assert.deepEqual(calls, [[intent.url, 'hin']]);
});

test('OTP completion resumes the saved stream and exact player without asking for the URL again', async () => {
  const backend = {
    request: async () => ({status: 'expired'}),
    resolve: async (url, lang) => {
      assert.equal(url, intent.url);
      assert.equal(lang, 'hin');
      return {url: 'http://127.0.0.1:8080/proxy/resumed/master.m3u8', shareId: 'resumed'};
    },
  };
  const paused = await prepareHotstarPlayback(backend, intent);
  const resumed = await resumeHotstarPlayback(backend, paused.pending);
  assert.equal(resumed.player, player);
  assert.equal(resumed.shareId, 'resumed');
  assert.match(resumed.url, /resumed/);
});

test('manual login completion returns to browsing without opening a stream', async () => {
  assert.equal(await resumeHotstarPlayback({resolve: () => assert.fail('Manual login has no pending stream.')}, null), null);
});

test('service errors remain visible instead of prompting for another account login', async () => {
  const backend = {request: async () => {throw new Error('Local service unavailable');}};
  await assert.rejects(prepareHotstarPlayback(backend, intent), /Local service unavailable/);
});

test('URL whitespace is normalized without mutating the original intent', async () => {
  const original = {url: '  ' + intent.url + '  ', player};
  const result = await prepareHotstarPlayback({request: async () => ({status: 'none'})}, original);
  assert.equal(result.pending.url, intent.url);
  assert.equal(result.pending.lang, 'eng');
  assert.equal(original.url, '  ' + intent.url + '  ');
});
