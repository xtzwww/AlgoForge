# ============================================================
#  AlgoForge launcher (PowerShell)
#
#  PU-3: this script no longer starts the sidecar.
#
#  Electron's main process owns the sidecar lifecycle now (see
#  desktop/src/main/sidecar.ts, PU-1): it picks a free random port,
#  generates the auth token, spawns uvicorn with `windowsHide` so no
#  console flashes, waits for /health, and tears down the whole process
#  tree on exit.
#
#  The old script started *its own* sidecar on a fixed port 8000 via
#  `Start-Process powershell.exe`. That caused three problems:
#    1. an extra console window (the "black boxes" at startup),
#    2. two sidecars running from two different code paths,
#    3. the two paths disagreed (fixed vs random port, --reload or not),
#       which made "works on my machine" reports impossible to trust.
#
#  What is left is what a launcher should be: put Node on PATH and hand
#  over to the dev server. Everything else is the app's job.
#
#  Prereqs:
#    - Node.js installed at d:\node
#    - sidecar/.venv created
#    - desktop/node_modules installed (npm install)
# ============================================================

$ErrorActionPreference = 'Stop'

$ProjectRoot = 'd:\BaiduNetdiskDownload\AlgoForge'
$DesktopDir  = Join-Path $ProjectRoot 'desktop'
$NodeDir     = 'd:\node'

# --- Fail fast with an actionable message ---------------------------
if (-not (Test-Path $DesktopDir)) {
    Write-Host "[error] desktop directory not found: $DesktopDir" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $DesktopDir 'node_modules'))) {
    Write-Host '[error] desktop/node_modules is missing.' -ForegroundColor Red
    Write-Host '        Run: cd desktop ; npm install' -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path (Join-Path $ProjectRoot 'sidecar\.venv\Scripts\python.exe'))) {
    Write-Host '[error] sidecar/.venv is missing.' -ForegroundColor Red
    Write-Host '        Run: cd sidecar ; python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}

# --- Node on PATH ---------------------------------------------------
# Appended rather than prepended so an already-working system install
# is not shadowed.
$env:PATH = "$env:PATH;$NodeDir;$NodeDir\node_modules\.bin"

# --- Hand over to the dev server ------------------------------------
# The sidecar is started by Electron; watch its log in this terminal
# under the "[sidecar]" prefix.
Write-Host '[start] AlgoForge (Electron + sidecar)...' -ForegroundColor Cyan
Write-Host '[hint]  sidecar 由 Electron 主进程拉起，日志前缀为 [sidecar]' -ForegroundColor DarkGray

Set-Location $DesktopDir
& npm run dev
