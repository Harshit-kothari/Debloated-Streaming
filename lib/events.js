export const FEEDS = {
  Fancode: 'https://raw.githubusercontent.com/drmlive/fancode-live-events/main/fancode.json',
  SonyLiv: 'https://raw.githubusercontent.com/drmlive/sliv-live-events/main/sonyliv.json',
};
export function parseFancodeTime(value) {
  const match = String(value || '').match(/(\d{2}):(\d{2}):(\d{2})\s(AM|PM)\s(\d{2})-(\d{2})-(\d{4})/i);
  if (!match) return 0;
  const [, hh, mm, ss, ampm, day, month, year] = match;
  let hour = Number(hh) % 12;
  if (ampm.toUpperCase() === 'PM') hour += 12;
  const date = Date.parse(`${year}-${month}-${day}T${String(hour).padStart(2, '0')}:${mm}:${ss}+05:30`);
  return Number.isFinite(date) ? date : 0;
}
export function normalizeMatches(fancode, sonyliv) {
  const matches = [];
  for (const [position, m] of fancode.entries()) {
    matches.push({id: `fc:${m.match_id ?? position}`, provider: 'Fancode', isLive: String(m.status).toUpperCase() === 'LIVE',
      title: m.match_name || 'Fancode', team1: m.team_1, team2: m.team_2,
      competition: m.event_name || '', startTime: m.startTime || null, startTimeMs: parseFancodeTime(m.startTime),
      language: m.audioLanguageName, poster: m.src, streamUrl: m.dai_url || m.adfree_url || null, userAgent: m['user-agent'] || ''});
  }
  for (const [position, m] of sonyliv.entries()) {
    matches.push({id: `sl:${m.contentId ?? position}`, provider: 'SonyLiv', isLive: m.isLive === true,
      title: m.match_name || m.event_name || 'SonyLiv', competition: m.event_name || '', startTimeMs: 0,
      language: m.audioLanguageName, poster: m.src, streamUrl: m.video_url || m.dai_url || null,
      userAgent: m['user-agent'] || ''});
  }
  return matches.sort((a, b) => Number(b.isLive) - Number(a.isLive) || a.startTimeMs - b.startTimeMs);
}
export async function loadMatches(fetchImpl = fetch, signal) {
  const results = await Promise.allSettled(Object.entries(FEEDS).map(async ([provider, url]) => {
    const timeout = AbortSignal.timeout(20000);
    const response = await fetchImpl(url, {signal: signal ? AbortSignal.any([signal, timeout]) : timeout});
    if (!response.ok) throw new Error(`${provider}: HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data.matches)) throw new Error(`${provider}: unexpected event data`);
    return data.matches;
  }));
  const warnings = results.flatMap((result, i) => result.status === 'rejected'
    ? [`${Object.keys(FEEDS)[i]} feed unavailable (${result.reason?.message || 'network error'}).`] : []);
  return {matches: normalizeMatches(...results.map(result => result.status === 'fulfilled' ? result.value : [])), warnings};
}
