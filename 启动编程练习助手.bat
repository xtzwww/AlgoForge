@echo off
rem ============================================================
rem  Programming Practice Assistant -- double-click launcher (fallback)
rem ============================================================
rem  Same job as the .vbs next to it. The only difference: a .bat
rem  flashes a console window for about half a second before closing.
rem  Use this one if the .vbs does nothing (some security software
rem  blocks .vbs).
rem
rem  2026-09-23: now launches dist-app-final\CodeLab.exe, so it no
rem  longer depends on desktop\node_modules.
rem
rem  ENCODING: ASCII prose + CRLF line endings, on purpose.
rem  This file used to be UTF-8 with Chinese comments and Unix LF
rem  endings. cmd reads a batch file by byte offset, so the LF endings
rem  desynchronised the parser and the Chinese in the comments made it
rem  worse -- double-clicking flashed a window and did nothing.
rem  Keep new prose ASCII, and keep the line endings CRLF.
rem ============================================================

setlocal

set "APPDIR=D:\BaiduNetdiskDownload\AlgoForge\dist-app-final"
set "APPEXE=%APPDIR%\CodeLab.exe"

if not exist "%APPEXE%" (
  echo.
  echo   Application not found:
  echo   %APPEXE%
  echo.
  echo   If the project was moved, open this file in Notepad
  echo   and update APPDIR to the new location.
  echo.
  pause
  exit /b 1
)

rem Set the working directory to the exe's own folder: the app looks
rem for D:\pg via a relative path when it starts PostgreSQL, and from
rem anywhere else it cannot find the data directory.
cd /d "%APPDIR%"
start "" "%APPEXE%"

endlocal
