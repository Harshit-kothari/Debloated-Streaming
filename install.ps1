# Debloated Streaming — Windows Installer
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "       Debloated Streaming — Windows Installer" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Node.js
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js is required but was not found in PATH." -ForegroundColor Red
    Write-Host "Install Node.js (v22+ recommended) from https://nodejs.org or via winget:" -ForegroundColor Yellow
    Write-Host "  winget install OpenJS.NodeJS.LTS" -ForegroundColor White
    exit 1
}

# 2. Check npm
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Error: npm was not found. Please ensure npm is installed with Node.js." -ForegroundColor Red
    exit 1
}

Write-Host "1. Installing Node dependencies..." -ForegroundColor Green
npm install

Write-Host "2. Setting up Python runtime and media players..." -ForegroundColor Green
npm run setup

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  Success! Debloated Streaming is ready to launch." -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Run 'npm start' or 'node index.js' to browse and stream." -ForegroundColor Yellow
Write-Host ""
