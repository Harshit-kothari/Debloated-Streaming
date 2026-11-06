#!/usr/bin/env node
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import fs from 'node:fs';
import {checkPython, PROJECT_DIR} from '../lib/backend.js';
import {discoverPlayers} from '../lib/players.js';

const execFileAsync = promisify(execFile);

async function isExecutable(filename) {
  try {
    const stat = await fs.promises.stat(filename);
    if (!stat.isFile()) return false;
    await fs.promises.access(filename, process.platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function runCommand(bin, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {stdio: 'inherit', windowsHide: true, ...options});
    child.on('error', reject);
    child.on('exit', (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`Command failed with exit code ${code}${signal ? ` (signal: ${signal})` : ''}`));
    });
  });
}

async function setup() {
  console.log('\n======================================================');
  console.log('   Debloated Streaming — Cross-Platform Environment Setup');
  console.log('======================================================\n');

  // 1. Detect Python
  console.log('[1/4] Checking Python 3.9+ runtime...');
  let python;
  try {
    python = await checkPython();
    console.log(`  ✓ Found Python: ${python.executable} (${python.python || python.version || 'ready'})`);
  } catch {
    console.error('\n[ERROR] Python 3.9+ was not found on your system.');
    console.error('Please install Python:');
    if (process.platform === 'win32') {
      console.error('  • Run: winget install Python.Python.3.12');
      console.error('  • Or download from: https://www.python.org/downloads/');
    } else if (process.platform === 'darwin') {
      console.error('  • Run: brew install python');
    } else {
      console.error('  • Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-pip python3-venv');
      console.error('  • Fedora:        sudo dnf install -y python3 python3-pip');
      console.error('  • Arch Linux:    sudo pacman -S python python-pip');
    }
    process.exit(1);
  }

  // 2. Ensure virtualenv if dependencies are missing and not in a venv
  const venvDir = path.join(PROJECT_DIR, '.venv');
  const venvPy = path.join(venvDir, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  let activePythonBin = python.command.bin;
  let activePythonArgs = [...python.command.args];
  const isReady = python && Array.isArray(python.missing) && python.missing.length === 0;

  if (!isReady && !fs.existsSync(venvDir) && !python.executable.includes('.venv')) {
    console.log('\n[2/4] Setting up Python virtual environment (.venv)...');
    try {
      await execFileAsync(python.command.bin, [...python.command.args, '-m', 'venv', venvDir], {
        cwd: PROJECT_DIR,
        windowsHide: true,
      });
      if (await isExecutable(venvPy)) {
        console.log('  ✓ Virtual environment created successfully.');
        activePythonBin = venvPy;
        activePythonArgs = [];
      }
    } catch {
      console.log('  ℹ Proceeding with direct installation (could not create .venv).');
    }
  } else if (fs.existsSync(venvPy)) {
    activePythonBin = venvPy;
    activePythonArgs = [];
  } else {
    console.log('\n[2/4] Python environment check passed.');
  }

  // 3. Install Requirements
  console.log('\n[3/4] Checking and installing Python dependencies...');
  if (isReady) {
    console.log('  ✓ All required Python dependencies are already installed.');
  } else {
    console.log(`  Installing packages: ${python.missing.join(', ')}...`);
    try {
      await runCommand(activePythonBin, [...activePythonArgs, '-m', 'pip', 'install', '-r', 'Requirements.txt'], {
        cwd: PROJECT_DIR,
      });
      console.log('  ✓ Python packages installed successfully.');
    } catch (pipErr) {
      console.error(`\n[ERROR] Failed to install dependencies: ${pipErr.message}`);
      process.exit(1);
    }
  }

  // 4. Discover Media Players
  console.log('\n[4/4] Checking installed media players (MPV / VLC)...');
  const players = await discoverPlayers();
  const available = players.filter(p => p.available);
  if (available.length > 0) {
    for (const p of available) {
      console.log(`  ✓ ${p.label} available: ${p.bin}`);
    }
  } else {
    console.log('\n  [NOTICE] Neither MPV nor VLC was detected.');
    console.log('  Debloated Streaming needs MPV (recommended) or VLC to play video streams.');
    console.log('  Install with one command:');
    if (process.platform === 'win32') {
      console.log('    winget install mpv.net');
      console.log('    # or');
      console.log('    winget install VideoLAN.VLC');
    } else if (process.platform === 'darwin') {
      console.log('    brew install mpv');
      console.log('    # or');
      console.log('    brew install --cask vlc');
    } else {
      console.log('    sudo apt install -y mpv    # Debian / Ubuntu / Mint');
      console.log('    sudo dnf install -y mpv    # Fedora / RHEL');
      console.log('    sudo pacman -S mpv         # Arch Linux');
    }
  }

  console.log('\n======================================================');
  console.log('   ✓ Debloated Streaming setup is ready!');
  console.log('======================================================\n');
  console.log('Start streaming right away:');
  console.log('  npm start');
  console.log('  # or: node index.js\n');
}

setup().catch(err => {
  console.error('\nSetup failed:', err.message || err);
  process.exit(1);
});
