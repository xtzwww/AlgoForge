@echo off
REM ============================================================
REM  AlgoForge 一键启动脚本
REM  双击即可拉起 sidecar (FastAPI) + desktop (Electron dev)
REM
REM  前置条件:
REM    - Node.js 已安装在 d:\node
REM    - sidecar/.venv 已创建 (python -m venv .venv)
REM    - desktop/node_modules 已安装 (npm install)
REM
REM  日志:
REM    - sidecar: sidecar\sidecar.log
REM    - desktop: 控制台窗口直接显示
REM ============================================================

setlocal

set "PROJECT_ROOT=d:\BaiduNetdiskDownload\AlgoForge"
set "SIDECAR_DIR=%PROJECT_ROOT%\sidecar"
set "DESKTOP_DIR=%PROJECT_ROOT%\desktop"
set "NODE_DIR=d:\node"

REM 把 node/npm 追加到 PATH（不覆盖系统 PATH，避免 cmd 内置命令丢失）
set "PATH=%PATH%;%NODE_DIR%;%NODE_DIR%\node_modules\.bin"

REM 启动 sidecar（后台窗口，日志写文件）
echo [启动] Sidecar (FastAPI)...
start "AlgoForge Sidecar" /D "%SIDECAR_DIR%" cmd /c ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > sidecar.log 2>&1"

REM 等 sidecar 起来（3 秒）
timeout /t 3 /nobreak > nul

REM 启动 desktop（前台，控制台显示日志）
echo [启动] Desktop (Electron)
echo [提示] 关闭此窗口会同时关闭前端；sidecar 窗口需单独关闭。
cd /d "%DESKTOP_DIR%"
call npm run dev

endlocal
