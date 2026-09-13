@echo off
REM ============================================================
REM  AlgoForge 启动脚本 (cmd)
REM
REM  PU-3: 本脚本不再启动 sidecar。
REM
REM  sidecar 的生命周期现在由 Electron 主进程负责
REM  (desktop/src/main/sidecar.ts, PU-1)：随机取空闲端口、生成 Token、
REM  以 windowsHide 方式 spawn uvicorn（不弹控制台）、等 /health 通过、
REM  退出时杀掉整棵进程树。
REM
REM  旧脚本用 `start "AlgoForge Sidecar" ... cmd /c` 自己拉一个固定
REM  8000 端口的 sidecar，带来三个问题：
REM    1. 多一个控制台黑框
REM    2. 两条启动路径各起一个 sidecar
REM    3. 两条路径行为还不一致（固定端口 vs 随机端口）
REM
REM  现在脚本只做一件事：把 Node 放进 PATH，然后把控制权交给 dev server。
REM
REM  前置条件:
REM    - Node.js 已安装在 d:\node
REM    - sidecar/.venv 已创建
REM    - desktop/node_modules 已安装 (npm install)
REM ============================================================

setlocal

set "PROJECT_ROOT=d:\BaiduNetdiskDownload\AlgoForge"
set "DESKTOP_DIR=%PROJECT_ROOT%\desktop"
set "NODE_DIR=d:\node"

REM --- 先做前置检查，失败时给出可执行的提示 ---------------------
if not exist "%DESKTOP_DIR%" (
    echo [error] desktop 目录不存在: %DESKTOP_DIR%
    exit /b 1
)

if not exist "%DESKTOP_DIR%\node_modules" (
    echo [error] desktop\node_modules 缺失。
    echo         请执行: cd desktop ^&^& npm install
    exit /b 1
)

if not exist "%PROJECT_ROOT%\sidecar\.venv\Scripts\python.exe" (
    echo [error] sidecar\.venv 缺失。
    echo         请执行: cd sidecar ^&^& python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

REM --- 把 Node 追加到 PATH（不覆盖系统 PATH）---------------------
set "PATH=%PATH%;%NODE_DIR%;%NODE_DIR%\node_modules\.bin"

REM --- 交给 dev server；sidecar 由 Electron 拉起 ------------------
echo [start] AlgoForge (Electron + sidecar)...
echo [hint]  sidecar 由 Electron 主进程拉起，日志前缀为 [sidecar]

cd /d "%DESKTOP_DIR%"
call npm run dev

endlocal
