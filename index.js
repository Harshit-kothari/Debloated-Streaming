#!/usr/bin/env node
import {Backend, checkPython} from './lib/backend.js';
import {discoverPlayers, resolvePlayer, launchPlayer} from './lib/players.js';
import {loadCatalogue} from './lib/catalogue.js';
import {prepareHotstarPlayback, resumeHotstarPlayback} from './lib/hotstar-flow.js';
const backend = new Backend();
const playback = new AbortController();
const help = `Debloated Streaming

  node index.js                          Open the terminal browser
  node index.js --doctor                 Check dependencies and players
  node index.js --login                  Log in with your phone and OTP
  node index.js --list                   Print the current event list
  node index.js --hotstar-live [--json]   Fetch live Hotstar titles and watch links
  node index.js --hotstar URL [--player mpv|vlc] [--lang eng|hin|tel|tam]

Browser: arrows navigate, Enter chooses a player, l logs in,
j opens a Hotstar URL, r refreshes, q quits. All files live here.
Set MPV_PATH, VLC_PATH or PYTHON_PATH to override an executable location.`;
function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (['--player', '--hotstar', '--lang'].includes(key)) {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error(`${key} requires a value.`);
      options[key.slice(2)] = argv[++i];
    } else if (['--help', '-h', '--doctor', '--login', '--list', '--hotstar-live', '--json'].includes(key)) options[key === '-h' ? 'help' : key.slice(2)] = true;
    else throw new Error(`Unknown option: ${key}. Use --help.`);
  }
  if (options.player && !['mpv', 'vlc'].includes(options.player.toLowerCase())) throw new Error('--player must be mpv or vlc.');
  return options;
}
async function doctor() {
  const [python, players] = await Promise.allSettled([checkPython(), discoverPlayers()]);
  console.log(python.status === 'fulfilled'
    ? `Python ${python.value.python} (${python.value.executable}): ${python.value.missing.length ? 'missing ' + python.value.missing.join(', ') : 'dependencies ready'}`
    : python.reason.message);
  if (players.status === 'fulfilled') for (const player of players.value) console.log(`${player.label}: ${player.available ? player.bin : player.error}`);
  let failed = python.status !== 'fulfilled' || python.value.missing.length;
  if (!failed) {
    try {
      const status = await backend.request('/api/token/status');
      console.log(`Hotstar login: ${status.status}${status.remaining ? ' (' + status.remaining + ')' : ''}. Use --login to sign in.`);
      console.log('Local relay: ready');
    } catch (error) {console.log(error.message); failed = true;}
  }
  if (players.status !== 'fulfilled' || !players.value.some(player => player.available)) failed = true;
  process.exitCode = failed ? 1 : 0;
}
async function login() {
  const {createInterface} = await import('node:readline/promises');
  const prompt = createInterface({input: process.stdin, output: process.stdout});
  try {
    const pending = await backend.request('/api/login/pending');
    let phone = pending.phone;
    if (pending.status !== 'pending') {
      phone = (await prompt.question('Indian mobile number (10 digits): ')).trim();
      await backend.request('/api/login/send_otp', {phone});
      console.log('Hotstar accepted the OTP request. Check your SMS.');
    } else {console.log('An OTP request is already pending. Enter that code to continue.');}
    const otp = (await prompt.question('OTP: ')).trim();
    const result = await backend.request('/api/login/verify_otp', {phone, otp});
    console.log(`Logged in. ${result.remaining || ''}`);
  } finally {prompt.close();}
}
async function play(player, url, options = {}) {
  const result = await launchPlayer(player, url, {...options, signal: playback.signal});
  if (result.code !== 0 && !playback.signal.aborted) throw new Error(`${player.label} exited with code ${result.code}.${result.stderr ? ' ' + result.stderr.trim().slice(-700) : ''}`);
}
async function terminalBrowser(options) {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    console.log('Open an interactive terminal to browse streams, or use --list, --login or --doctor.');
    return;
  }
  const [{default: React, useState, useEffect, useCallback, useRef}, {render, Box, Text, useInput, useApp, useStdout}, {default: terminalImage}] = await Promise.all([
    import('react'), import('ink'), import('terminal-image'),
  ]);
  const h = React.createElement;
  const players = await discoverPlayers();
  const defaultPlayer = Math.max(0, players.findIndex(player => player.available));
  const posterCache = new Map();
  function Poster({url, width}) {
    const [image, setImage] = useState(null);
    useEffect(() => {
      let active = true; setImage(null);
      if (!url) return;
      const controller = new AbortController();
      const cacheKey = `${url}:${width}`;
      (async () => {
        try {
          let result = posterCache.get(cacheKey);
          if (!result) {
            const response = await fetch(url, {signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])});
            if (!response.ok) return;
            result = await terminalImage.buffer(Buffer.from(await response.arrayBuffer()), {width, height: 10, preserveAspectRatio: true, preferNativeRender: false});
            if (posterCache.size > 30) posterCache.delete(posterCache.keys().next().value);
            posterCache.set(cacheKey, result);
          }
          if (active) setImage(result);
        } catch {}
      })();
      return () => {active = false; controller.abort();};
    }, [url, width]);
    return h(Box, {height: 11, overflow: 'hidden'}, h(Text, {dimColor: !image}, image || '(poster unavailable)'));
  }
  function App() {
    const {exit} = useApp(); const {stdout} = useStdout();
    const [matches, setMatches] = useState([]), [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(true), [message, setMessage] = useState('Fetching live streams...');
    const [warning, setWarning] = useState(''), [authStatus, setAuthStatus] = useState({status: 'checking'});
    const [mode, setMode] = useState('browse'), [input, setInput] = useState(''), [phone, setPhone] = useState('');
    const [candidate, setCandidate] = useState(null), [playerIndex, setPlayerIndex] = useState(defaultPlayer);
    const [pendingHotstar, setPendingHotstar] = useState(null);
    const [busy, setBusy] = useState(false); const locked = useRef(false);
    const feedRequest = useRef(null);
    const refreshing = useRef(false);
    const mounted = useRef(true);
    const refreshAuthStatus = useCallback(async () => {
      try {
        const data = await backend.request('/api/token/status');
        if (mounted.current) setAuthStatus(data);
      } catch {
        if (mounted.current) setAuthStatus({status: 'unavailable'});
      }
    }, []);
    const refresh = useCallback(async (forceRefresh = false) => {
      if (locked.current || refreshing.current) return;
      refreshing.current = true;
      feedRequest.current?.abort();
      const controller = new AbortController();
      feedRequest.current = controller;
      setLoading(true);
      let result;
      try {result = await loadCatalogue(backend, {signal: controller.signal, refresh: forceRefresh});}
      catch (error) {if (!controller.signal.aborted) {setWarning(error.message); setLoading(false);} return;}
      finally {refreshing.current = false;}
      if (controller.signal.aborted) return;
      setMatches(result.matches); setWarning(result.warnings.join(' ')); setIndex(0); setLoading(false);
      setMessage(result.matches.length ? `${result.hotstarCount} Hotstar live events found. Select an event to play, or press j for a URL.` : 'No events found. Login and Hotstar URLs are still available.');
    }, []);
    useEffect(() => {
      mounted.current = true;
      refresh();
      refreshAuthStatus();
      const statusTimer = setInterval(refreshAuthStatus, 60000);
      return () => {mounted.current = false; clearInterval(statusTimer); feedRequest.current?.abort();};
    }, [refresh, refreshAuthStatus]);
    const run = async action => {
      if (locked.current) return;
      locked.current = true; setBusy(true);
      try {await action();} catch (error) {setMessage(error.message);}
      finally {locked.current = false; setBusy(false);}
    };
    const openPlayer = async (player, url, userAgent = '') => {
      setMode('browse'); setMessage(`${player.label} is playing. Close its window to return here.`);
      await play(player, url, {userAgent});
      setMessage('Player closed. Select another stream.');
    };
    const promptLogin = async () => {
      const pending = await backend.request('/api/login/pending');
      setInput('');
      if (pending.status === 'pending') {
        setPhone(pending.phone); setMode('otp');
        setMessage('An OTP request is pending. Enter the code, or press r to resend after the cooldown.');
      } else {
        setMode('phone');
        setMessage('Enter your 10-digit mobile number. Hotstar playback will continue after login.');
      }
    };
    useInput((text, key) => {
      if ((key.ctrl && text === 'c') || (!['phone', 'otp', 'url'].includes(mode) && text === 'q')) {playback.abort(); exit(); return;}
      if (busy) return;
      if (['phone', 'otp', 'url'].includes(mode)) {
        if (key.escape) {setMode('browse'); setInput(''); setPendingHotstar(null); setMessage('Cancelled. Select an event or press j for Hotstar.'); return;}
        if (mode === 'otp' && text.toLowerCase() === 'r') {
          run(async () => {
            setMessage('Requesting another OTP...');
            await backend.request('/api/login/send_otp', {phone});
            setInput(''); setMessage('Hotstar accepted the new OTP request. Check your SMS.');
          });
          return;
        }
        if (key.backspace || key.delete) {setInput(value => value.slice(0, -1)); return;}
        if (key.return) {
          const value = input.trim(); if (!value) return;
          if (mode === 'phone') run(async () => {
            setMessage('Requesting OTP...'); await backend.request('/api/login/send_otp', {phone: value});
            setPhone(value); setInput(''); setMode('otp'); setMessage('Hotstar accepted the OTP request. Enter the code, or press r to resend.');
          });
          else if (mode === 'otp') run(async () => {
            setMessage('Verifying OTP...');
            const data = await backend.request('/api/login/verify_otp', {phone, otp: value});
            setAuthStatus({status: 'valid', remaining: data.remaining}); setInput(''); setPhone('');
            if (pendingHotstar) {
              setMessage('Logged in. Opening your Hotstar stream...');
              // If content resolution fails, retry playback without repeating OTP.
              setMode('player');
              setPendingHotstar(null);
              const ready = await resumeHotstarPlayback(backend, pendingHotstar);
              await openPlayer(ready.player, ready.url);
            } else {setMode('browse'); setMessage('Logged in. Press r to load Hotstar live events, or j for a URL.');}
          });
          else {setCandidate({provider: 'JioHotstar', title: 'JioHotstar', hotstarUrl: value}); setPlayerIndex(defaultPlayer); setMode('player'); setInput(''); setMessage('Choose a player.');}
          return;
        }
        if (!key.ctrl && !key.meta && text && !/[\x00-\x1f\x7f]/.test(text)) {
          const addition = mode === 'url' ? text : text.replace(/\D/g, '');
          setInput(value => (value + addition).slice(0, mode === 'url' ? 2048 : mode === 'phone' ? 10 : 8));
        }
        return;
      }
      if (mode === 'player') {
        if (key.escape) {setMode('browse'); return;}
        if (key.upArrow) setPlayerIndex(value => Math.max(0, value - 1));
        if (key.downArrow) setPlayerIndex(value => Math.min(players.length - 1, value + 1));
        if (key.return) run(async () => {
          const player = players[playerIndex]; if (!player.available) throw new Error(player.error);
          let url = candidate.streamUrl;
          if (candidate.hotstarUrl) {
            setMessage('Checking Hotstar login and preparing playback...');
            const ready = await prepareHotstarPlayback(backend, {url: candidate.hotstarUrl, player, lang: options.lang || 'eng'});
            if (ready.requiresLogin) {
              setPendingHotstar(ready.pending);
              await promptLogin();
              return;
            }
            url = ready.url;
          }
          await openPlayer(player, url, candidate.userAgent || '');
        });
        return;
      }
      if (key.escape) {exit(); return;}
      if (text.toLowerCase() === 'l') {setPendingHotstar(null); run(promptLogin);}
      if (text.toLowerCase() === 'j') {setPendingHotstar(null); setMode('url'); setInput(''); setMessage('Paste a JioHotstar live URL. Login will be requested if needed.');}
      if (text.toLowerCase() === 'r') {refreshAuthStatus(); refresh(true);}
      if (key.upArrow) setIndex(value => Math.max(0, value - 1));
      if (key.downArrow) setIndex(value => Math.min(matches.length - 1, value + 1));
      if (key.return && (matches[index]?.streamUrl || matches[index]?.hotstarUrl)) {setCandidate(matches[index]); setPlayerIndex(defaultPlayer); setMode('player');}
    });
    const selected = matches[index], width = stdout.columns || 100;
    const rows = Math.max(3, Math.min(18, (stdout.rows || 30) - 14));
    const first = Math.max(0, Math.min(index - Math.floor(rows / 2), matches.length - rows));
    const loginLabel = authStatus.status === 'valid'
      ? `NOT EXPIRED${authStatus.remaining ? ' · ' + authStatus.remaining : ''}`
      : authStatus.status === 'expired' ? 'EXPIRED · press l to log in again'
      : authStatus.status === 'none' ? 'NOT LOGGED IN · press l to log in'
      : authStatus.status === 'checking' ? 'Checking...'
      : 'STATUS UNAVAILABLE · press r to retry';
    const loginColor = authStatus.status === 'valid' ? 'green' : authStatus.status === 'expired' ? 'red' : 'yellow';
    return h(Box, {flexDirection: 'column'},
      h(Text, {bold: true, color: 'cyan'}, 'Debloated Streaming'),
      h(Text, {bold: true, color: loginColor}, `Hotstar login: ${loginLabel}`),
      h(Text, {color: 'magenta'}, 'JioHotstar: live events listed below · [r] Refresh · [j] Paste URL · [l] Log in'),
      h(Box, {flexDirection: 'row'},
        h(Box, {flexDirection: 'column', width: width >= 85 ? '50%' : '100%', borderStyle: 'round', borderColor: 'cyan', paddingX: 1},
          h(Text, {bold: true}, `Live / Upcoming (${matches.length})`),
          loading ? h(Text, null, 'Loading live events from Hotstar and community feeds...') : matches.length ? matches.slice(first, first + rows).map((match, offset) => h(Text, {
            key: match.id, color: match.isLive ? 'red' : 'yellow', bold: first + offset === index,
            backgroundColor: first + offset === index ? '#202020' : undefined, wrap: 'truncate-end',
          }, `${first + offset === index ? '> ' : '  '}${match.isLive ? '●' : '◷'} ${match.provider} · ${match.title}`)) : h(Text, {dimColor: true}, 'No events available')),
        width >= 85 ? h(Box, {flexDirection: 'column', width: '50%', borderStyle: 'round', borderColor: 'green', paddingX: 1},
          h(Poster, {url: selected?.poster, width: Math.max(16, Math.floor(width / 2) - 6)}),
          h(Text, {bold: true}, selected?.title || 'JioHotstar'),
          h(Text, {dimColor: true}, selected?.competition || 'Press l to log in, j for a live URL.'),
          selected ? h(Text, {color: selected.isLive ? 'red' : 'yellow'}, selected.isLive ? 'LIVE NOW' : `UPCOMING ${selected.startTime || ''}`) : null,
          selected?.language ? h(Text, null, `Language: ${selected.language}`) : null,
          selected ? h(Text, {dimColor: true}, selected.streamUrl || selected.hotstarUrl ? 'Enter to play' : 'Stream not yet available') : null) : null),
      mode === 'player' ? h(Box, {flexDirection: 'column', borderStyle: 'round', paddingX: 1},
        h(Text, {bold: true}, `Play ${candidate?.title} with:`),
        ...players.map((player, i) => h(Text, {key: player.key, inverse: i === playerIndex, color: player.available ? 'green' : 'gray'}, `${i === playerIndex ? '> ' : '  '}${player.label}${player.available ? '' : ' (not found)'}`)),
        h(Text, {dimColor: true}, 'Arrows choose · Enter play · Esc cancel')) : null,
      ['phone', 'otp', 'url'].includes(mode) ? h(Box, {borderStyle: 'round', paddingX: 1}, h(Text, null, `${mode === 'phone' ? 'Phone' : mode === 'otp' ? 'OTP' : 'Hotstar URL'} > ${mode === 'otp' ? '*'.repeat(input.length) : input}█`)) : null,
      h(Text, {color: busy ? 'cyan' : 'yellow', wrap: 'wrap'}, message),
      warning ? h(Text, {color: 'yellow'}, warning) : null,
      h(Text, {dimColor: true}, '↑/↓ navigate · Enter play · l login · j Hotstar URL · r refresh · q quit'));
  }
  await render(h(App)).waitUntilExit();
}
async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {console.log(help); return;}
  if (options.doctor) return doctor();
  if (options.login) return login();
  if (options['hotstar-live']) {
    const result = await backend.request('/api/hotstar/live?refresh=1', undefined, 60000);
    if (options.json) {console.log(JSON.stringify(result, null, 2)); return;}
    for (const match of result.matches) console.log(`${match.title}\n${match.hotstarUrl}\n`);
    console.log(`${result.matches.length} live Hotstar events found${result.partial ? ' (partial catalogue)' : ''}.`);
    for (const warning of result.warnings || []) console.error(warning);
    return;
  }
  if (options.list) {
    const result = await loadCatalogue(backend);
    if (options.json) {console.log(JSON.stringify(result, null, 2)); return;}
    for (const match of result.matches) console.log(`${match.isLive ? 'LIVE' : 'UPCOMING'} | ${match.provider} | ${match.title}`);
    for (const warning of result.warnings) console.error(warning);
    if (!result.matches.length) {console.log('No events found. Use --login or --hotstar URL for JioHotstar.'); if (result.warnings.length) process.exitCode = 1;}
    return;
  }
  if (options.hotstar) {
    const players = await discoverPlayers();
    const player = options.player ? await resolvePlayer(options.player) : players.find(item => item.available);
    if (!player) throw new Error(players.map(item => item.error).join('\n'));
    console.log('Preparing JioHotstar playback...');
    let resolved = await prepareHotstarPlayback(backend, {url: options.hotstar, player, lang: options.lang || 'eng'});
    if (resolved.requiresLogin) {
      if (!process.stdin.isTTY) throw new Error('Hotstar login is required. Run node index.js in an interactive terminal and enter your phone and OTP.');
      console.log('Hotstar login is required; playback will continue after verification.');
      await login();
      resolved = await resumeHotstarPlayback(backend, resolved.pending);
    }
    console.log(`Playing with ${player.label}. Close the player to finish.`); return play(player, resolved.url);
  }
  return terminalBrowser(options);
}
process.once('exit', () => backend.stop());
process.once('SIGTERM', () => {playback.abort(); backend.stop(); process.exit(143);});
process.once('SIGINT', () => {playback.abort(); backend.stop(); process.exit(130);});
try {await main();}
catch (error) {if (!playback.signal.aborted) {console.error(error.message); process.exitCode = 1;}}
finally {backend.stop(); process.stdin.pause(); process.stdin.unref?.();}
