import test from 'node:test';
import assert from 'node:assert/strict';
import {loadMatches, normalizeMatches, parseFancodeTime} from '../lib/events.js';

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

test('malformed feeds produce useful warnings instead of a rejected refresh', async () => {
  const result = await loadMatches(async () => ({ok: true, json: async () => ({unexpected: []})}));
  assert.equal(result.matches.length, 0);
  assert.equal(result.warnings.length, 2);
});
