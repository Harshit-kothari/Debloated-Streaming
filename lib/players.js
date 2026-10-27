import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';

const runFile = promisify(execFile);
const PROJECT_DIR = fileURLToPath(new URL('../', import.meta.url));
const DEFINITIONS = [
  { key: 'mpv', label: 'MPV', variable: 'MPV_PATH', args: ['--cache=yes', '--demuxer-max-bytes=60MiB', '--demuxer-readahead-secs=15', '--network-timeout=20', '--input-terminal=no'] },
  { key: 'vlc', label: 'VLC', variable: 'VLC_PATH', args: ['--network-caching=4000', '--play-and-exit'] },
];

async function isExecutable(filename, platform) {
  try {
    const stat = await fs.stat(filename);
    if (!stat.isFile()) return false;
    await fs.access(filename, platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
    return true;
  } catch { return false; }
}

function unquote(value) {
  const text = String(value || '').trim();
  return /^(["']).*\1$/.test(text) ? text.slice(1, -1) : text;
}

async function onPath(command, env, platform) {
  if (platform === 'win32') {
    try {
      const { stdout } = await runFile('where.exe', [command], {
        env, windowsHide: true, timeout: 4000, maxBuffer: 65536, shell: false,
      });
      for (const filename of stdout.split(/\r?\n/).filter(Boolean)) {
        if (await isExecutable(filename.trim(), platform)) return filename.trim();
      }
    } catch { /* Search the supplied PATH below, including in tests. */ }
  }
  const pathValue = env.PATH || env.Path || '';
  for (const directory of pathValue.split(platform === 'win32' ? ';' : ':').filter(Boolean)) {
    const filename = path.join(unquote(directory), command);
    if (await isExecutable(filename, platform)) return filename;
  }
  return null;
}

async function wingetCandidates(key, env) {
  if (!env.LOCALAPPDATA) return [];
  const root = path.join(env.LOCALAPPDATA, 'Microsoft', 'WinGet', 'Packages');
  let packages;
  try { packages = await fs.readdir(root, { withFileTypes: true }); } catch { return []; }
  const results = [];
  async function visit(directory, depth) {
    results.push(path.join(directory, `${key}.exe`));
    if (!depth) return;
    let children;
    try { children = await fs.readdir(directory, { withFileTypes: true }); } catch { return; }
    for (const child of children) {
      if (child.isDirectory() && !child.isSymbolicLink()) await visit(path.join(directory, child.name), depth - 1);
    }
  }
  for (const item of packages) {
    if (item.isDirectory() && item.name.toLowerCase().includes(key)) {
      await visit(path.join(root, item.name), 3);
    }
  }
  return results;
}

function missingPlayer(definition, details = '') {
  const filename = `${definition.key}${process.platform === 'win32' ? '.exe' : ''}`;
  return `${details ? `${details} ` : ''}${definition.label} was not found. Install ${definition.label}, put ${filename} in tools/${definition.key}/, or set ${definition.variable} to its executable's full path.`;
}

/** Discover both players. Missing entries remain visible with available:false and an error. */
export async function discoverPlayers(options = {}) {
  const env = options.env || process.env;
  const platform = options.platform || process.platform;
  const projectDir = options.projectDir || PROJECT_DIR;
  return Promise.all(DEFINITIONS.map(async definition => {
    const result = { ...definition, args: [...definition.args], bin: null, available: false };
    const command = `${definition.key}${platform === 'win32' ? '.exe' : ''}`;
    const override = unquote(env[definition.variable]);
    if (override) {
      const filename = /[\\/]/.test(override) ? path.resolve(projectDir, override) : await onPath(override, env, platform);
      if (filename && await isExecutable(filename, platform)) return { ...result, bin: filename, available: true };
      return { ...result, error: missingPlayer(definition, `${definition.variable} does not point to an executable.`) };
    }
    const candidates = [path.join(projectDir, 'tools', definition.key, command), path.join(projectDir, 'tools', command)];
    if (platform === 'win32') {
      const local = env.LOCALAPPDATA;
      const home = env.USERPROFILE;
      for (const base of [env.ProgramW6432, env.ProgramFiles, env['ProgramFiles(x86)']].filter(Boolean)) {
        candidates.push(path.join(base, definition.key === 'vlc' ? 'VideoLAN/VLC' : 'mpv', command));
      }
      if (local) {
        candidates.push(path.join(local, definition.key, command), path.join(local, 'Programs', definition.key, command));
        if (definition.key === 'vlc') candidates.push(path.join(local, 'Programs', 'VideoLAN', 'VLC', command));
        candidates.push(path.join(local, 'Microsoft', 'WinGet', 'Links', command));
      }
      if (home) candidates.push(path.join(home, 'scoop', 'apps', definition.key, 'current', command));
    } else if (platform === 'darwin') {
      candidates.push(definition.key === 'vlc' ? '/Applications/VLC.app/Contents/MacOS/VLC' : '/opt/homebrew/bin/mpv');
    }
    for (const filename of candidates) {
      if (await isExecutable(filename, platform)) return { ...result, bin: filename, available: true };
    }
    const fromPath = await onPath(command, env, platform);
    if (fromPath) return { ...result, bin: fromPath, available: true };
    if (platform === 'win32') {
      for (const filename of await wingetCandidates(definition.key, env)) {
        if (await isExecutable(filename, platform)) return { ...result, bin: filename, available: true };
      }
    }
    return { ...result, error: missingPlayer(definition) };
  }));
}

/** Resolve a requested player, rejecting with installation/configuration instructions if missing. */
export async function resolvePlayer(key, options = {}) {
  const player = (await discoverPlayers(options)).find(item => item.key === String(key).toLowerCase());
  if (!player) throw new Error('Choose mpv or vlc.');
  if (!player.available) throw new Error(player.error);
  return player;
}

function checkedHeader(name, value) {
  if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || /[\r\n\0]/.test(String(value))) {
    throw new Error('Invalid playback HTTP header.');
  }
  return String(value);
}

/** Build separate argv entries; the stream URL is never interpreted by a shell. */
export function buildPlayerArgs(player, url, { userAgent = '', headers = {} } = {}) {
  if (!url || typeof url !== 'string' || /[\r\n\0]/.test(url)) throw new Error('A valid stream URL or media path is required.');
  if (!DEFINITIONS.some(item => item.key === player?.key)) throw new Error('Choose mpv or vlc.');
  const args = [...(player.args || DEFINITIONS.find(item => item.key === player.key).args)];
  const fields = Object.entries(headers || {}).map(([name, value]) => [name, checkedHeader(name, value)]);
  const ua = userAgent || fields.find(([name]) => name.toLowerCase() === 'user-agent')?.[1];
  if (ua) args.push(`${player.key === 'mpv' ? '--user-agent' : '--http-user-agent'}=${checkedHeader('User-Agent', ua)}`);
  for (const [name, value] of fields) {
    if (name.toLowerCase() === 'user-agent') continue;
    if (player.key === 'mpv') {
      // The append operation accepts one complete value, including literal commas.
      args.push(`--http-header-fields-append=${name}: ${value}`);
    } else if (['referer', 'referrer'].includes(name.toLowerCase())) {
      args.push(`--http-referrer=${value}`);
    } else {
      throw new Error(`VLC cannot forward the ${name} header. Choose MPV for this stream.`);
    }
  }
  return [...args, '--', url];
}

function cleanStderr(text, url, headers) {
  let cleaned = text.split(url).join('[stream URL]');
  for (const [name, value] of Object.entries(headers || {})) {
    if (/authorization|cookie|token|key/i.test(name) && value) cleaned = cleaned.split(String(value)).join('[redacted]');
  }
  return cleaned.replace(/https?:\/\/[^\s"'<>]+/gi, '[URL]').replace(/((?:token|auth|signature|cookie|key)\s*[=:]\s*)[^\s,;]+/gi, '$1[redacted]').slice(-8192);
}

/** Launch without terminal stdin or shell expansion. Resolve once the child closes. */
export function launchPlayer(player, url, options = {}) {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(Object.assign(new Error('Playback cancelled.'), { name: 'AbortError', code: 'ABORT_ERR' }));
      return;
    }
    if (!player?.bin || player.available === false) { reject(new Error(player?.error || 'Player executable is missing.')); return; }
    let child;
    let stderr = '';
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      if (error) reject(error); else resolve(result);
    };
    let args;
    try { args = buildPlayerArgs(player, url, options); } catch (error) { finish(error); return; }
    try {
      child = (options.spawn || spawn)(player.bin, args, {
        stdio: ['ignore', 'pipe', 'pipe'], shell: false, windowsHide: true,
        ...(options.env ? { env: options.env } : {}),
        ...(options.signal ? { signal: options.signal } : {}),
      });
    } catch (error) { finish(new Error(`Could not launch ${player.label || player.key}: ${error.code || 'invalid player configuration'}.`)); return; }
    // MPV normally logs playback failures on stdout, whereas VLC uses stderr.
    // Keep a single bounded diagnostic stream for callers and keep Ink's UI clean.
    const pending = { stdout: '', stderr: '' };
    const append = text => { stderr = (stderr + cleanStderr(text, url, options.headers)).slice(-8192); };
    const record = stream => chunk => {
      const lines = (pending[stream] + chunk.toString()).split('\n');
      pending[stream] = lines.pop();
      for (const line of lines) append(line + '\n');
      // A broken player may emit one huge line. Drop it intact so chopping the
      // front cannot expose the tail of a signed URL or authentication header.
      if (pending[stream].length > 65536) pending[stream] = '[oversized diagnostic line omitted]';
    };
    child.stdout?.on('data', record('stdout'));
    child.stderr?.on('data', record('stderr'));
    child.once('error', error => {
      if (error.name === 'AbortError' || error.code === 'ABORT_ERR') {
        finish(Object.assign(new Error('Playback cancelled.'), { name: 'AbortError', code: 'ABORT_ERR' }));
        return;
      }
      finish(new Error(`Could not launch ${player.label || player.key}: ${error.code || 'process startup failed'}. Check ${player.key.toUpperCase()}_PATH.`));
    });
    child.once('close', (code, signal) => {
      append(pending.stdout);
      append(pending.stderr);
      finish(null, { code: code ?? 1, signal: signal || null, stderr });
    });
  });
}
