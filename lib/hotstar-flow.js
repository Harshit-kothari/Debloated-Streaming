/** Keep the chosen URL, language and player while an account login is completed. */
function playbackIntent({url, player, lang = 'eng'} = {}) {
  if (typeof url !== 'string' || !url.trim()) throw new Error('Enter a JioHotstar live URL.');
  if (!player) throw new Error('Choose a player before opening JioHotstar.');
  return {url: url.trim(), player, lang};
}

/** Check account status before resolving; callers can display the inline login form. */
export async function prepareHotstarPlayback(backend, intent) {
  const pending = playbackIntent(intent);
  const status = await backend.request('/api/token/status');
  if (status.status !== 'valid') return {requiresLogin: true, pending};
  return {requiresLogin: false, pending, ...await resumeHotstarPlayback(backend, pending)};
}

/** Continue the exact playback request after OTP verification, or return to browsing. */
export async function resumeHotstarPlayback(backend, pending) {
  if (!pending) return null;
  const intent = playbackIntent(pending);
  const resolved = await backend.resolve(intent.url, intent.lang);
  return {...resolved, player: intent.player};
}
