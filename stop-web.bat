@echo off
REM ============================================================
REM  CodeLab / 编程练习助手 -- Web edition stopper
REM
REM  Stops only the processes this suite started, never touching
REM  other Python or database programs:
REM    - static server listening on 127.0.0.1:5179
REM    - sidecar       listening on 127.0.0.1:8123
REM    - PostgreSQL    listening on 127.0.0.1:5432  (optional)
REM
REM  Do NOT use "taskkill /IM python.exe": that would kill your
REM  other Python programs too. Everything here resolves the PID
REM  by port instead.
REM
REM  Created 2026-09-24 (PU-12).
REM
REM  ENCODING: save this file as UTF-8 WITH BOM.
REM ============================================================

setlocal enabledelayedexpansion

set "SIDECAR_PORT=8123"
set "WEB_PORT=5179"

REM  Set to 0 to keep PostgreSQL running; keeping it makes the
REM  next start much faster.
set "STOP_PG=1"

echo.
echo   ============================================
echo     编程练习助手 网页版 停止
echo   ============================================
echo.

call :kill_port %WEB_PORT% "网页服务"
call :kill_port %SIDECAR_PORT% "后端 sidecar"

if "%STOP_PG%"=="1" goto stop_pg
echo   [数据库] 按配置保留，STOP_PG=0
goto after_pg

:stop_pg
REM  Stop PG with pg_ctl fast mode: a clean shutdown flushes WAL.
REM  A hard taskkill works too but the next start replays recovery,
REM  which is slow and gets worse if repeated.
echo   [数据库] 正在关闭 PostgreSQL
set "COMSPEC=D:\pg\nocmd.exe"
"D:\pg\pgsql\bin\pg_ctl.exe" -D "D:\pg\data" -m fast stop >nul 2>&1
if errorlevel 1 (
  echo             未停止，可能本来就没在运行
) else (
  echo             已关闭
)

:after_pg
echo.
echo   完成
echo.
pause
exit /b 0

REM ---- helper: kill whatever listens on a port -----------------
REM  This label must stay OUTSIDE every parenthesised block,
REM  otherwise cmd reports ") was unexpected at this time.".
:kill_port
REM  %1 = port, %2 = display name
set "FOUND=0"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"127.0.0.1:%1 .*LISTENING"') do (
  set "FOUND=1"
  echo   [%2] 端口 %1 上是 PID %%p，正在结束
  taskkill /F /PID %%p >nul 2>&1
)
if "!FOUND!"=="0" echo   [%2] 端口 %1 上没有服务在运行
if "!FOUND!"=="1" echo             已结束
exit /b 0
