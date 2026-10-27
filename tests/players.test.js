import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { buildPlayerArgs, discoverPlayers, launchPlayer, resolvePlayer } from '../lib/players.js';

const mpv = { key: 'mpv', label: 'MPV', bin: 'test-mpv', available: true, args: [] };

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sports-player-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

async function executable(filename) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  await fs.writeFile(filename, 'test executable placeholder');
  await fs.chmod(filename, 0o755);
  return filename;
}

test('discovers a project-local player from any working directory', async t => {
  const root = await fixture(t);
  const bin = await executable(path.join(root, 'tools', 'mpv', 'mpv.exe'));
  const players = await discoverPlayers({ projectDir: root, platform: 'win32', env: { PATH: '' } });
  assert.equal(players[0].bin, bin);
  assert.equal(players[0].available, true);
  assert.equal(players[1].available, false);
  assert.match(players[1].error, /VLC_PATH/);
});

test('explicit paths containing spaces are honored and invalid overrides are actionable', async t => {
  const root = await fixture(t);
  const bin = await executable(path.join(root, 'My Player', 'mpv.exe'));
  const options = { projectDir: root, platform: 'win32', env: { MPV_PATH: `"${bin}"`, PATH: '' } };
  assert.equal((await resolvePlayer('mpv', options)).bin, bin);
  await assert.rejects(resolvePlayer('mpv', { ...options, env: { MPV_PATH: path.join(root, 'missing.exe'), PATH: '' } }), /MPV_PATH does not point to an executable/);
});

test('discovers MPV in a nested WinGet installation directory', async t => {
  const root = await fixture(t);
  const local = path.join(root, 'Local');
  const bin = await executable(path.join(local, 'Microsoft', 'WinGet', 'Packages', 'mpv.net_mpv', 'mpv-x64', 'mpv.exe'));
  const players = await discoverPlayers({ projectDir: root, platform: 'win32', env: { LOCALAPPDATA: local, PATH: '' } });
  assert.equal(players[0].bin, bin);
});

test('HTTP headers and URL remain separate arguments, with literal commas preserved', () => {
  const url = 'https://cdn.example/live.m3u8?x=1&y=$(echo injected)';
  const args = buildPlayerArgs(mpv, url, { userAgent: 'Example Agent', headers: { Origin: 'https://example.com', Accept: 'video/mp2t, */*' } });
  assert.deepEqual(args, ['--user-agent=Example Agent', '--http-header-fields-append=Origin: https://example.com', '--http-header-fields-append=Accept: video/mp2t, */*', '--', url]);
  assert.throws(() => buildPlayerArgs(mpv, url, { headers: { Origin: 'value\r\nInjected: header' } }), /Invalid playback HTTP header/);
});

test('VLC receives its supported UA and referrer flags and reports unsupported headers', () => {
  const player = { key: 'vlc', args: [] };
  assert.deepEqual(buildPlayerArgs(player, 'https://example.com/live', { headers: { 'User-Agent': 'Agent', Referer: 'https://example.com' } }), ['--http-user-agent=Agent', '--http-referrer=https://example.com', '--', 'https://example.com/live']);
  assert.throws(() => buildPlayerArgs(player, 'https://example.com', { headers: { Origin: 'https://example.com' } }), /Choose MPV/);
});

test('launch errors reject once even when close follows and do not expose the URL', async () => {
  const child = new EventEmitter();
  child.stderr = new PassThrough();
  let spawnOptions;
  const url = 'https://example.com/private?token=secret';
  const pending = launchPlayer(mpv, url, { spawn: (_bin, args, options) => {
    spawnOptions = options;
    assert.equal(args.at(-1), url);
    queueMicrotask(() => { child.emit('error', Object.assign(new Error('private'), { code: 'ENOENT' })); child.emit('close', -1); });
    return child;
  } });
  await assert.rejects(pending, error => /ENOENT/.test(error.message) && !error.message.includes('secret'));
  assert.equal(spawnOptions.shell, false);
  assert.deepEqual(spawnOptions.stdio, ['ignore', 'pipe', 'pipe']);
});

test('launch captures failure diagnostics with secrets redacted and a size bound', async () => {
  const child = new EventEmitter();
  child.stderr = new PassThrough();
  const url = 'https://example.com/private?token=secret';
  const pending = launchPlayer(mpv, url, { headers: { Authorization: 'Bearer private-secret' }, spawn: () => {
    queueMicrotask(() => {
      child.stderr.write('x'.repeat(12000));
      child.stderr.write(`\nfailed ${url}\nAuthorization: Bearer private-secret\n`);
      child.emit('close', 2, null);
    });
    return child;
  } });
  const result = await pending;
  assert.equal(result.code, 2);
  assert.equal(result.signal, null);
  assert.ok(result.stderr.length <= 8192);
  assert.match(result.stderr, /failed \[stream URL\]/);
  assert.ok(!result.stderr.includes('secret'));
});

test('missing executable rejects with setup instructions', async () => {
  await assert.rejects(launchPlayer({ key: 'mpv', available: false, error: 'Set MPV_PATH.' }, 'https://example.com'), /Set MPV_PATH/);
});

test('MPV failures written to stdout are captured and redacted for callers', async () => {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const url = 'https://example.com/private?token=stdout-secret';
  const result = await launchPlayer(mpv, url, { spawn: () => {
    queueMicrotask(() => {
      child.stdout.write(`Failed to open ${url}\nHTTP error 403 Forbidden\n`);
      child.emit('close', 2, null);
    });
    return child;
  } });
  assert.equal(result.code, 2);
  assert.match(result.stderr, /HTTP error 403 Forbidden/);
  assert.match(result.stderr, /\[stream URL\]/);
  assert.ok(!result.stderr.includes('stdout-secret'));
});

test('long signed URLs split across chunks are redacted before bounding diagnostics', async () => {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  const url = 'https://cdn.example/master.m3u8';
  const result = await launchPlayer(mpv, url, { spawn: () => {
    queueMicrotask(() => {
      child.stdout.write('Opening https://cdn.example/');
      child.stdout.write('private'.repeat(2000));
      child.stdout.write('/segment.ts?signature=private-secret\nHTTP error 403 Forbidden\n');
      child.emit('close', 2, null);
    });
    return child;
  } });
  assert.ok(result.stderr.length <= 8192);
  assert.ok(!result.stderr.includes('private'));
  assert.match(result.stderr, /HTTP error 403 Forbidden/);
});

test('an already aborted signal prevents starting the player', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(launchPlayer(mpv, 'https://example.com', { signal: controller.signal, spawn: () => assert.fail('spawn must not run') }), { name: 'AbortError', code: 'ABORT_ERR' });
});

test('aborting playback terminates the real child without an unhandled process error', async () => {
  const controller = new AbortController();
  const player = { ...mpv, bin: process.execPath, args: ['-e', 'setInterval(() => {}, 1000)'] };
  const pending = launchPlayer(player, 'https://example.com', { signal: controller.signal });
  const timer = setTimeout(() => controller.abort(), 50);
  try {
    await assert.rejects(pending, { name: 'AbortError', code: 'ABORT_ERR' });
  } finally { clearTimeout(timer); }
});
