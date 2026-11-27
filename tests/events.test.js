import test from 'node:test';
import assert from 'node:assert/strict';
import {loadMatches, normalizeMatches, parseFancodeTime, parseSonyPlaylist} from '../lib/events.js';

const sonyBackup = `#EXTM3U
#EXTINF:-1 tvg-id="123" tvg-logo="https://example.test/logo,a.jpg" group-title="sonyliv-Cricket", India, New Zealand [ENG]
#EXTVLCOPT:http-user-agent=Feed browser agent
https://example.test/eng.m3u8?signature=fixture
#EXTINF:-1 tvg-id="123" group-title="sonyliv-Cricket", India, New Zealand [HIN]
https://example.test/hin.m3u8
`;

test('event feeds fail independently and keep the working provider', async () => {
  const result = await loadMatches(async url => {
    if (url.includes('fancode')) throw new Error('offline');
    return {ok: true, json: async () => ({matches: [{contentId: 1, match_name: 'Live match', isLive: true, video_url: 'https://example.test/live.m3u8'}]})};
  });
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0].provider, 'SonyLiv');
  assert.match(result.warnings[0], /Fancode/);
});

test('upcoming events without playback URLs retain their schedule title', () => {
  const matches = normalizeMatches([{match_id: 1, match_name: 'Later', status: 'UPCOMING'}],
    [{contentId: 2, event_name: 'Upcoming cricket', isLive: false}, {contentId: 3, match_name: 'Now', isLive: true}]);
  assert.equal(matches.length, 3);
  assert.equal(matches[0].title, 'Now');
  assert.equal(matches.find(m => m.id === 'sl:2').title, 'Upcoming cricket');
  assert.equal(matches.find(m => m.id === 'fc:1').streamUrl, null);
});

test('feed times use India time across noon and midnight', () => {
  assert.equal(parseFancodeTime('12:00:00 AM 27-10-2026'), Date.parse('2026-10-26T18:30:00Z'));
  assert.equal(parseFancodeTime('12:00:00 PM 27-10-2026'), Date.parse('2026-10-27T06:30:00Z'));
  assert.equal(parseFancodeTime('unknown'), 0);
});

test('Sony playback supplies a CDN-compatible user agent while preserving feed overrides', () => {
  const matches = normalizeMatches([], [
    {contentId: 1, isLive: true, video_url: 'https://example.test/live.m3u8'},
    {contentId: 2, isLive: true, video_url: 'https://example.test/other.m3u8', 'user-agent': 'Feed-specified agent'},
  ]);
  assert.match(matches[0].userAgent, /Mozilla.*Chrome/);
  assert.equal(matches[1].userAgent, 'Feed-specified agent');
  assert.equal(normalizeMatches([{match_id: 3}], [])[0].userAgent, '');
});

test('malformed feeds produce useful warnings instead of a rejected refresh', async () => {
  const result = await loadMatches(async () => ({ok: true, json: async () => ({unexpected: []})}));
  assert.equal(result.matches.length, 0);
  assert.equal(result.warnings.length, 2);
});

test('Sony backup preserves titles, language variants, comma-containing metadata and stream URLs', () => {
  const events = parseSonyPlaylist('\ufeff' + sonyBackup.replace(/\n/g, '\r\n'));
  const matches = normalizeMatches([], events);
  assert.equal(matches.length, 2);
  assert.equal(matches[0].title, 'India, New Zealand [ENG]');
  assert.equal(matches[0].poster, 'https://example.test/logo,a.jpg');
  assert.equal(matches[0].streamUrl, 'https://example.test/eng.m3u8?signature=fixture');
  assert.equal(matches[0].userAgent, 'Feed browser agent');
  assert.equal(matches[0].language, 'ENG');
  assert.equal(matches[1].language, 'HIN');
  assert.notEqual(matches[0].id, matches[1].id);
  assert.ok(matches.every(match => match.isLive));
});

test('a truncated Sony JSON feed falls back without losing Fancode or exposing raw response text', async () => {
  const result = await loadMatches(async url => {
    if (url.includes('fancode')) return {ok: true, json: async () => ({matches: [{match_id: 1, status: 'LIVE'}]})};
    if (url.endsWith('.m3u')) return {ok: true, text: async () => sonyBackup};
    return {ok: true, json: async () => {throw new SyntaxError('Truncated JSON containing private-signature');}};
  });
  assert.equal(result.matches.filter(match => match.provider === 'Fancode').length, 1);
  assert.equal(result.matches.filter(match => match.provider === 'SonyLiv').length, 2);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /M3U backup \(2 streams\)/);
  assert.ok(!result.warnings[0].includes('private-signature'));
});

test('a healthy Sony JSON feed, including an empty event list, never requests the backup', async () => {
  const calls = [];
  const result = await loadMatches(async url => {
    calls.push(url);
    assert.ok(!url.endsWith('.m3u'));
    return {ok: true, json: async () => ({matches: []})};
  });
  assert.equal(calls.length, 2);
  assert.deepEqual(result, {matches: [], warnings: []});
});

test('the backup rejects non-playlist responses and ignores incomplete or non-HTTP entries', () => {
  assert.throws(() => parseSonyPlaylist('<html>unavailable</html>'), /M3U playlist/);
  const events = parseSonyPlaylist(sonyBackup + `
#EXTINF:-1, Local file
file:///C:/private.txt
#EXTINF:-1, Script
javascript:alert(1)
#EXTINF:-1, Broken URL
https:
#EXTINF:-1, Incomplete entry
`);
  assert.equal(events.length, 2);
});

test('Sony source failures preserve Fancode and report both unavailable sources', async () => {
  const result = await loadMatches(async url => {
    if (url.includes('fancode')) return {ok: true, json: async () => ({matches: [{match_id: 1}]})};
    return {ok: false, status: 503};
  });
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0].provider, 'Fancode');
  assert.match(result.warnings[0], /JSON feed and M3U backup unavailable/);
});

test('cancelled requests do not start the Sony backup request', async () => {
  const controller = new AbortController();
  let backupRequested = false;
  await loadMatches(async url => {
    if (url.endsWith('.m3u')) backupRequested = true;
    if (url.includes('sonyliv.json')) {controller.abort(); throw new Error('cancelled');}
    return {ok: true, json: async () => ({matches: []})};
  }, controller.signal);
  assert.equal(backupRequested, false);
});
