import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const runFile = promisify(execFile);
export const hotstarBrowser = Object.freeze({key: 'browser', label: 'Hotstar website (browser · DRM)', available: true});

/** Open only an official content page; never forward tokens or media URLs. */
export function hotstarWatchUrl(input) {
  let url;
  try {url = new URL(input.trim());} catch {throw new Error('Enter a valid official Hotstar content URL.');}
  if (url.protocol !== 'https:' || !['www.hotstar.com', 'hotstar.com'].includes(url.hostname)
      || url.port || url.username || url.password || !url.pathname.startsWith('/in/')
      || !/^\/[A-Za-z0-9_/-]+$/.test(url.pathname) || !/\/\d{9,12}(?:\/|$)/.test(url.pathname)) {
    throw new Error('Enter a valid official Hotstar content URL.');
  }
  return 'https://www.hotstar.com' + url.pathname;
}

export async function openHotstarInBrowser(input, {platform = process.platform, run = runFile} = {}) {
  const url = hotstarWatchUrl(input);
  const command = platform === 'win32' ? ['rundll32.exe', ['url.dll,FileProtocolHandler', url]]
    : platform === 'darwin' ? ['open', [url]] : ['xdg-open', [url]];
  try {
    await run(command[0], command[1], {shell: false, windowsHide: true, timeout: 15000, maxBuffer: 65536});
  } catch {
    throw new Error(`Could not open your browser. Open this page manually: ${url}`);
  }
  return url;
}
