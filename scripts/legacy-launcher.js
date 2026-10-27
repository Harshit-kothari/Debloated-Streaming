#!/usr/bin/env node
// Compatibility entry point for the former neighboring livesports-tui project.
// This source is installed as livesports-tui/index.js; all app code stays in
// jio-hotstar-stream-proxy so fixes and saved login are shared.
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const entry = new URL('../jio-hotstar-stream-proxy/index.js', import.meta.url);
if (!existsSync(fileURLToPath(entry))) {
  console.error('Unified launcher not found. Keep jio-hotstar-stream-proxy beside livesports-tui, or run node index.js inside jio-hotstar-stream-proxy.');
  process.exitCode = 1;
} else {
  await import(entry.href);
}
