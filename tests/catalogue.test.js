import test from 'node:test';
import assert from 'node:assert/strict';
import {loadCatalogue} from '../lib/catalogue.js';

const watchUrl = 'https://www.hotstar.com/in/sports/cricket/live-match/123456789/live';
const hotstarMatch = {id: 'hs:123456789', provider: 'JioHotstar', isLive: true, title: 'Hotstar match', hotstarUrl: watchUrl, streamUrl: null, startTimeMs: 0};
const emptyFeeds = async () => ({ok: true, json: async () => ({matches: []})});

test('combines all providers and keeps live events before upcoming events', async () => {
  const fetchImpl = async url => ({ok: true, json: async () => ({matches: url.includes('fancode')
    ? [{match_id: 1, match_name: 'Upcoming FC', status: 'UPCOMING'}, {match_id: 2, match_name: 'Live FC', status: 'LIVE'}]
    : [{contentId: 3, match_name: 'Live Sony', isLive: true}]})});
  const requests = [];
  const backend = {request: async (...args) => {requests.push(args); return {matches: [hotstarMatch], warnings: [], partial: false};}};
  const result = await loadCatalogue(backend, {fetchImpl});
  assert.equal(result.matches.length, 4);
  assert.deepEqual(result.matches.slice(0, 3).map(match => match.provider), ['Fancode', 'SonyLiv', 'JioHotstar']);
  assert.equal(result.matches.at(-1).title, 'Upcoming FC');
  assert.equal(result.hotstarCount, 1);
  assert.equal(result.partial, false);
  assert.deepEqual(requests, [['/api/hotstar/live', undefined, 60000]]);
});

test('Hotstar login errors preserve community feeds and provide an actionable warning', async () => {
  const backend = {request: async () => {throw new Error('Log in to JioHotstar before listing live events.');}};
  const fetchImpl = async url => ({ok: true, json: async () => ({matches: url.includes('fancode')
    ? [{match_id: 1, match_name: 'Live FC', status: 'LIVE'}] : []})});
  const result = await loadCatalogue(backend, {fetchImpl});
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0].provider, 'Fancode');
  assert.equal(result.hotstarCount, 0);
  assert.equal(result.partial, true);
  assert.match(result.warnings[0], /Log in to JioHotstar/);
});

test('community failures keep Hotstar and provider warnings mark partial results', async () => {
  const backend = {request: async () => ({matches: [hotstarMatch], warnings: ['A live tray could not be loaded.'], partial: true})};
  const result = await loadCatalogue(backend, {fetchImpl: async () => {throw new Error('offline');}});
  assert.deepEqual(result.matches, [hotstarMatch]);
  assert.equal(result.hotstarCount, 1);
  assert.equal(result.partial, true);
  assert.equal(result.warnings.length, 3);
  assert.ok(result.warnings.some(warning => warning.includes('live tray')));
});

test('duplicates by ID or canonical watch link are removed, and refresh reaches the service', async () => {
  const backend = {request: async route => {
    assert.equal(route, '/api/hotstar/live?refresh=1');
    return {matches: [hotstarMatch, {...hotstarMatch, title: 'Duplicate ID'},
      {...hotstarMatch, id: 'hs:other', hotstarUrl: watchUrl + '?source=home#live'}], warnings: []};
  }};
  const result = await loadCatalogue(backend, {fetchImpl: emptyFeeds, refresh: true});
  assert.deepEqual(result.matches, [hotstarMatch]);
  assert.equal(result.hotstarCount, 1);
});

test('pre-cancelled refresh starts neither feeds nor the backend', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(loadCatalogue({request: () => assert.fail('No backend call after cancellation.')}, {
    signal: controller.signal, fetchImpl: () => assert.fail('No feed call after cancellation.'),
  }), {name: 'AbortError'});
});

test('cancellation discards completed results instead of updating the UI', async () => {
  const controller = new AbortController();
  let finish;
  const backend = {request: () => new Promise(resolve => {finish = resolve;})};
  const pending = loadCatalogue(backend, {signal: controller.signal, fetchImpl: emptyFeeds});
  await Promise.resolve();
  controller.abort();
  finish({matches: [hotstarMatch]});
  await assert.rejects(pending, {name: 'AbortError'});
});

test('unexpected responses and warning URLs cannot leak signed playback links', async () => {
  const backend = {request: async () => {throw new Error('Failed at https://cdn.example/live.m3u8?token=private-token');}};
  const result = await loadCatalogue(backend, {fetchImpl: emptyFeeds});
  assert.match(result.warnings[0], /\[URL\]/);
  assert.ok(!result.warnings[0].includes('private-token'));
  const malformed = await loadCatalogue({request: async () => ({wrong: []})}, {fetchImpl: emptyFeeds});
  assert.equal(malformed.partial, true);
  assert.match(malformed.warnings[0], /unexpected catalogue response/);
});
