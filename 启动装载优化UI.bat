@echo off
rem ============================================================
rem  Container Load Optimizer - UI Launcher
rem  Double-click to start the web console and open the browser.
rem  Close the server window (or press Ctrl+C) to stop the service.
rem ============================================================
setlocal
chcp 65001 >nul 2>&1
title Container Load Optimizer

set "SKILL=%USERPROFILE%\.workbuddy\skills\container-load-optimizer"
set "PORT=8770"
set "URL=http://127.0.0.1:%PORT%"

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python was not found in PATH.
  echo         Install Python 3.9+ or add python.exe to PATH, then run this file again.
  pause
  exit /b 1
)

if not exist "%SKILL%\ui\server.py" (
  echo [ERROR] Skill not found at:
  echo         %SKILL%
  echo         Check the path and try again.
  pause
  exit /b 1
)

rem --- already running? then just open the browser ---
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo Service already running on %PORT%. Opening browser...
  start "" "%URL%"
  exit /b 0
)

echo Starting server on port %PORT% ...
echo Workspace: %SKILL%\ui\workspace
echo.
echo Keep this window open while using the UI.
echo Press Ctrl+C in this window to stop the service.
echo.

start "LoadOpt Server (port %PORT%)" cmd /k "python ""%SKILL%\ui\server.py"" --port %PORT%"

rem wait for the port to come up, then open the browser
rem (ping is used as the delay: unlike "timeout", it is never shadowed by a
rem  GNU coreutils binary that may sit earlier on PATH inside Git Bash)
set /a tries=0
:waitloop
ping -n 2 127.0.0.1 >nul 2>&1
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :ready
set /a tries+=1
if %tries% lss 15 goto :waitloop
echo [WARN] Server did not respond within 15s. Check the server window for errors.
pause
exit /b 1

:ready
echo.
echo ============================================================
echo  UI ready:  %URL%
echo  Close the "LoadOpt Server" window to stop the service.
echo ============================================================
start "" "%URL%"
endlocal
