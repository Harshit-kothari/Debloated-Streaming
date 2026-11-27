import test from 'node:test';
import assert from 'node:assert/strict';
import {hotstarWatchUrl, openHotstarInBrowser} from '../lib/browser.js';

const page = 'https://www.hotstar.com/in/sports/cricket/match/1271234567/live';

test('browser handoff forwards only the official content page without query credentials', () => {
  assert.equal(hotstarWatchUrl(page + '?token=private#player'), page);
  assert.equal(hotstarWatchUrl(page.replace('www.', '')), page);
});

test('browser handoff rejects non-Hotstar URLs, media URLs and shell-like input before launch', async () => {
  for (const url of [null, 'file:///C:/Windows/system32/cmd.exe', 'javascript:alert(1)',
    'https://hotstar.com.evil.test/in/sports/1271234567/live',
    'https://name:secret@www.hotstar.com/in/sports/1271234567/live',
    'http://www.hotstar.com/in/sports/1271234567/live',
    'https://www.hotstar.com:8080/in/sports/1271234567/live',
    'https://cdn.hotstar.com/media/master.m3u8',
    page + ' & calc.exe']) {
    await assert.rejects(openHotstarInBrowser(url, {run: () => assert.fail('Invalid URL launched.')}), /valid official Hotstar/);
  }
});

test('browser handoff uses a separate URL argument and no shell on each platform', async () => {
  for (const [platform, executable, prefix] of [
    ['win32', 'rundll32.exe', ['url.dll,FileProtocolHandler']],
    ['darwin', 'open', []], ['linux', 'xdg-open', []],
  ]) {
    const opened = await openHotstarInBrowser(page, {platform, run: async (file, args, options) => {
      assert.equal(file, executable);
      assert.deepEqual(args, [...prefix, page]);
      assert.equal(options.shell, false);
      assert.equal(options.windowsHide, true);
    }});
    assert.equal(opened, page);
  }
});

test('a failed browser launch reports a usable content page without credentials', async () => {
  await assert.rejects(openHotstarInBrowser(page + '?token=private', {run: async () => {throw new Error('failed');}}), error => {
    assert.ok(error.message.endsWith(page));
    assert.ok(!error.message.includes('private'));
    return true;
  });
});
