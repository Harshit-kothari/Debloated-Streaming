export const FEEDS = {
  Fancode: 'https://raw.githubusercontent.com/drmlive/fancode-live-events/main/fancode.json',
  SonyLiv: 'https://raw.githubusercontent.com/drmlive/sliv-live-events/main/sonyliv.json',
};
const SONY_M3U_FEED = 'https://raw.githubusercontent.com/drmlive/sliv-live-events/main/sonyliv.m3u';
// Sony's CDN rejects VLC's default User-Agent, including for clear HLS.
const SONY_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36';
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
      userAgent: m['user-agent'] || SONY_USER_AGENT});
  }
  return matches.sort((a, b) => Number(b.isLive) - Number(a.isLive) || a.startTimeMs - b.startTimeMs);
}

/** Read complete channel entries from the same publisher's backup playlist. */
export function parseSonyPlaylist(text) {
  if (typeof text !== 'string' || !text.trimStart().startsWith('#EXTM3U')) {
    throw new Error('SonyLiv backup did not return an M3U playlist.');
  }
  const matches = [];
  let entry = null;
  for (const line of text.split(/\r?\n/).map(line => line.trim())) {
    if (line.startsWith('#EXTINF:')) {
      // A quoted attribute (such as a logo URL) can itself contain commas.
      const metadata = line.match(/^#EXTINF:((?:[^",]|"[^"]*")*),(.*)$/);
      entry = null;
      if (!metadata) continue;
      const attrs = Object.fromEntries([...metadata[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
      const title = metadata[2].trim() || attrs['tvg-name'];
      if (!title) continue;
      const language = title.match(/\[([a-z]{2,3})\]/i)?.[1].toUpperCase();
      entry = {contentId: `${attrs['tvg-id'] || title}:${language || ''}`, isLive: true,
        match_name: title, event_name: (attrs['group-title'] || '').replace(/^sonyliv-/i, ''),
        audioLanguageName: language, src: attrs['tvg-logo']};
    } else if (entry && line.startsWith('#EXTVLCOPT:http-user-agent=')) {
      entry['user-agent'] = line.slice('#EXTVLCOPT:http-user-agent='.length);
    } else if (entry && line && !line.startsWith('#')) {
      try {
        const url = new URL(line);
        if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
          matches.push({...entry, video_url: line});
        }
      } catch { /* Incomplete or invalid entries never become playback links. */ }
      entry = null;
    }
  }
  return matches;
}

export async function loadMatches(fetchImpl = fetch, signal) {
  const fetchFeed = async (provider, url) => {
    const timeout = AbortSignal.timeout(20000);
    const response = await fetchImpl(url, {signal: signal ? AbortSignal.any([signal, timeout]) : timeout});
    if (!response.ok) throw new Error(`${provider}: HTTP ${response.status}`);
    return response;
  };
  const results = await Promise.allSettled(Object.entries(FEEDS).map(async ([provider, url]) => {
    try {
      const response = await fetchFeed(provider, url);
      let data;
      try {data = await response.json();}
      catch {throw new Error(`${provider}: invalid or incomplete JSON`);}
      if (!Array.isArray(data?.matches)) throw new Error(`${provider}: unexpected event data`);
      return {matches: data.matches, warnings: []};
    } catch (error) {
      if (provider !== 'SonyLiv' || signal?.aborted) throw error;
      try {
        const response = await fetchFeed(provider, SONY_M3U_FEED);
        const matches = parseSonyPlaylist(await response.text());
        if (!matches.length) throw new Error('No complete backup entries.');
        return {matches, warnings: [`SonyLiv JSON feed unavailable; using its M3U backup (${matches.length} streams).`]};
      } catch {
        throw new Error('SonyLiv: JSON feed and M3U backup unavailable.');
      }
    }
  }));
  const warnings = results.flatMap((result, i) => result.status === 'rejected'
    ? [`${Object.keys(FEEDS)[i]} feed unavailable (${result.reason?.message || 'network error'}).`] : result.value.warnings);
  return {matches: normalizeMatches(...results.map(result => result.status === 'fulfilled' ? result.value.matches : [])), warnings};
}
