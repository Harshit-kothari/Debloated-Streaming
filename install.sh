#!/usr/bin/env bash
set -e

echo ""
echo "======================================================"
echo "       Debloated Streaming — Installer"
echo "======================================================"
echo ""

# 1. Check Node.js
if ! command -v node >/dev/null 2>&1; then
  echo "Error: Node.js is required but not installed."
  echo "Please install Node.js (v22+ recommended) from https://nodejs.org or via your package manager."
  exit 1
fi

NODE_MAJOR=$(node -v | cut -d'.' -f1 | tr -d 'v')
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "Warning: Node.js 18+ (22+ recommended) is required. Detected: $(node -v)"
fi

# 2. Check npm
if ! command -v npm >/dev/null 2>&1; then
  echo "Error: npm was not found. Please ensure npm is installed with Node.js."
  exit 1
fi

echo "1. Installing Node dependencies..."
npm install

echo "2. Setting up Python runtime and media players..."
npm run setup

echo ""
echo "======================================================"
echo "  Success! Debloated Streaming is ready to launch."
echo "======================================================"
echo ""
echo "Run 'npm start' or 'node index.js' to browse and stream."
echo ""
