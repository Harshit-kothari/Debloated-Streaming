import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import {randomBytes} from 'node:crypto';
import readline from 'node:readline';

const execFileAsync = promisify(execFile);
export const PROJECT_DIR = fileURLToPath(new URL('../', import.meta.url));

export function pythonCandidates(env = process.env) {
  if (env.PYTHON_PATH) return [{bin: env.PYTHON_PATH, args: []}];
  const local = path.join(PROJECT_DIR, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return [
    ...(fs.existsSync(local) ? [{bin: local, args: []}] : []),
    {bin: 'python', args: []},
    ...(process.platform === 'win32' ? [{bin: 'py', args: ['-3']}] : [{bin: 'python3', args: []}]),
  ];
}

export async function checkPython() {
  let lastError;
  let missingRuntime;
  for (const candidate of pythonCandidates()) {
    try {
      const {stdout} = await execFileAsync(candidate.bin, [...candidate.args, path.join(PROJECT_DIR, 'service.py'), '--check'],
        {cwd: PROJECT_DIR, windowsHide: true, timeout: 15000, encoding: 'utf8'});
      return {...JSON.parse(stdout.trim()), executable: candidate.bin, command: candidate};
    } catch (error) {
      // A working Python without dependencies is more useful than a missing command.
      try {
        const result = JSON.parse(error.stdout?.trim());
        if (result.missing) missingRuntime ??= {...result, executable: candidate.bin, command: candidate};
      } catch {}
      lastError = error;
    }
  }
  if (missingRuntime) return missingRuntime;
  throw new Error('Python 3.9+ was not found. Install Python or set PYTHON_PATH to python.exe.', {cause: lastError});
}

export class Backend {
  child = null;
  starting = null;
  baseUrl = null;
  generation = 0;
  requests = new AbortController();
  token = randomBytes(24).toString('hex');

  async start() {
    if (this.child && this.baseUrl) return;
    if (this.starting) return this.starting;
    this.starting = this._start().finally(() => { this.starting = null; });
    return this.starting;
  }

  async _start() {
    const generation = this.generation;
    if (this.requests.signal.aborted) this.requests = new AbortController();
    const python = await checkPython();
    if (generation !== this.generation) throw new Error('Local service startup cancelled.');
    if (python.missing.length) throw new Error(`Missing Python packages: ${python.missing.join(', ')}. Run python -m pip install -r Requirements.txt`);
    const child = spawn(python.command.bin, [...python.command.args, '-u', path.join(PROJECT_DIR, 'service.py')], {
      cwd: PROJECT_DIR, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env, PYTHONIOENCODING: 'utf-8', HOTSTAR_SERVICE_TOKEN: this.token},
    });
    this.child = child;
    child.stdin.on('error', () => {});
    // Relay logs may contain signed media URLs. Keep them out of the TUI.
    child.stderr.resume();
    try {
      await new Promise((resolve, reject) => {
        const lines = readline.createInterface({input: child.stdout});
        const timer = setTimeout(() => reject(new Error('Local Hotstar service did not start within 20 seconds.')), 20000);
        const failed = () => reject(new Error('Local Hotstar service stopped before it was ready. Run python service.py --check.'));
        child.once('error', reject);
        child.once('exit', failed);
        lines.on('line', line => {
          try {
            const data = JSON.parse(line);
            if (data.error) reject(new Error(data.error));
            else if (data.ready && Number.isInteger(data.port)) {
              this.baseUrl = `http://127.0.0.1:${data.port}`;
              resolve();
            }
          } catch {}
        });
        const cleanup = () => {clearTimeout(timer); child.removeListener('exit', failed);};
        // Both readiness and failure must clear the timer.
        lines.once('close', cleanup);
        this._readyCleanup = cleanup;
      });
    } catch (error) {
      this.stop();
      throw error;
    } finally {
      this._readyCleanup?.();
    }
    if (generation !== this.generation) {this.stop(); throw new Error('Local service startup cancelled.');}
    child.once('exit', () => {if (this.child === child) {this.child = null; this.baseUrl = null;}});
  }

  async request(route, data, timeout = 180000) {
    await this.start();
    let response;
    try {
      response = await fetch(this.baseUrl + route, {
        method: data === undefined ? 'GET' : 'POST',
        headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + this.token},
        ...(data === undefined ? {} : {body: JSON.stringify(data)}),
        signal: AbortSignal.any([this.requests.signal, AbortSignal.timeout(timeout)]),
      });
    } catch (error) {
      throw new Error(error.name === 'TimeoutError' ? 'Hotstar took too long to respond. Try again.' : 'Local Hotstar service could not be reached. Try again.', {cause: error});
    }
    let result;
    try {result = await response.json();}
    catch {throw new Error(`Local service returned an invalid response (HTTP ${response.status}). Restart the launcher and try again.`);}
    if (!response.ok || result.error) throw new Error(result.error || `Local service returned HTTP ${response.status}.`);
    return result;
  }

  async resolve(url, lang = 'eng') {
    const result = await this.request('/api/create', {url, lang});
    // Keep the relay running for the entire player session.
    return {url: new URL(result.m3u8_proxy, this.baseUrl).href, shareId: result.share_id};
  }

  stop() {
    this.generation++;
    this.requests.abort();
    const child = this.child;
    this.child = null;
    this.baseUrl = null;
    if (child) {child.stdin.end(); child.kill();}
  }
}
