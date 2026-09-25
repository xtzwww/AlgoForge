@echo off
rem ============================================================
rem  编程练习助手 —— 双击启动（备选方案）
rem ============================================================
rem  与同目录的 .vbs 作用相同，区别只有一个：
rem  双击 .bat 会闪过一个命令行黑框（约半秒，随即自动关闭）。
rem  如果 .vbs 双击没反应（有些安全软件会拦 .vbs），就用这个。
rem
rem  2026-09-23 改：改为启动 dist-app-final\CodeLab.exe，
rem  不再依赖 desktop\node_modules。
rem ============================================================

setlocal

set "APPDIR=D:\BaiduNetdiskDownload\AlgoForge\dist-app-final"
set "APPEXE=%APPDIR%\CodeLab.exe"

if not exist "%APPEXE%" (
  echo.
  echo   找不到应用：
  echo   %APPEXE%
  echo.
  echo   如果项目被移动过，请用记事本打开这个文件，
  echo   把 APPDIR 改到新位置。
  echo.
  pause
  exit /b 1
)

rem 工作目录设成 exe 所在目录：应用启动 PostgreSQL 时要用相对路径
rem 找 D:\pg，从别处启动会找不到数据目录。
cd /d "%APPDIR%"
start "" "%APPEXE%"

endlocal
