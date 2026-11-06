#!/usr/bin/env node
import {spawn} from 'node:child_process';
import {checkPython, PROJECT_DIR} from '../lib/backend.js';

try {
  const python = await checkPython();
  const child = spawn(python.command.bin, [...python.command.args, ...process.argv.slice(2)], {
    cwd: PROJECT_DIR,
    stdio: 'inherit',
    windowsHide: true,
  });
  child.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
    } else {
      process.exit(code ?? 0);
    }
  });
} catch (err) {
  console.error(err.message || err);
  process.exit(1);
}
