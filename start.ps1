# ============================================================
#  AlgoForge launcher (PowerShell)
#  Double-click the desktop shortcut to start sidecar + desktop.
#
#  Prereqs:
#    - Node.js installed at d:\node
#    - sidecar/.venv created
#    - desktop/node_modules installed
#
#  Note on ports: the sidecar defaults to a RANDOM free port and a
#  random auth token (AGENTS.md: "Sidecar 监听随机端口 + Token 鉴权").
#  This script pins a fixed port for convenience during development so
#  the log file and any curl/browser testing stay predictable. Set
#  $SidecarPort = 0 to fall back to the random-port behavior.
# ============================================================

$ErrorActionPreference = 'Stop'

# --- Path constants ---
$ProjectRoot = 'd:\BaiduNetdiskDownload\AlgoForge'
$SidecarDir  = Join-Path $ProjectRoot 'sidecar'
$DesktopDir  = Join-Path $ProjectRoot 'desktop'
$NodeDir     = 'd:\node'
$SidecarPort = 8000

# --- Append node/npm to PATH ---
$env:PATH = "$env:PATH;$NodeDir;$NodeDir\node_modules\.bin"

# --- Start sidecar (background process, log to file) ---
Write-Host '[start] Sidecar (FastAPI)...' -ForegroundColor Cyan
$sidecarLog = Join-Path $SidecarDir 'sidecar.log'
$sidecarProc = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList "-NoProfile -Command `"& { Set-Location '$SidecarDir'; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port $SidecarPort --reload *>&1 | Tee-Object -FilePath '$sidecarLog' }`"" `
    -WindowStyle Minimized -PassThru

Write-Host "[info] Sidecar PID: $($sidecarProc.Id), port: $SidecarPort, log: $sidecarLog"

# --- Wait for sidecar to come up (3s) ---
Write-Host '[wait] 3s for sidecar to start...' -ForegroundColor Yellow
Start-Sleep -Seconds 3

# --- Start desktop (foreground, logs to console) ---
Write-Host '[start] Desktop (Electron)...' -ForegroundColor Cyan
Write-Host '[hint] Closing this window stops desktop. Sidecar window must be closed separately.' -ForegroundColor Yellow

Set-Location $DesktopDir
& npm run dev

# --- Clean up sidecar after desktop exits ---
if (-not $sidecarProc.HasExited) {
    Write-Host '[cleanup] Stopping Sidecar...' -ForegroundColor Yellow
    Stop-Process -Id $sidecarProc.Id -Force -ErrorAction SilentlyContinue
}
