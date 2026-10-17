@echo off
REM Order Exception Agent - one-click demo for Windows.
REM Installs Python packages, starts n8n + the backend, opens the dashboard and the n8n editor.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" (
  echo Python is not installed. Install Python 3.11+ from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

echo Using: & %PY% --version
echo.
echo Installing Python packages...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Package install failed - see the message above.
  pause
  exit /b 1
)

echo.
where npx >nul 2>nul
if errorlevel 1 (
  echo Node.js not found, so the built-in Python engine runs the process instead of n8n.
  echo Install Node.js 20-24 from https://nodejs.org to use n8n.
  start "" cmd /c "timeout /t 8 >nul & start http://localhost:8001"
  %PY% scripts\dev.py --python
) else (
  echo Starting n8n and the backend. The first run downloads n8n, which takes a few minutes.
  echo The browser opens when everything is ready.
  start "" cmd /c "%PY% scripts\wait_ready.py && start http://localhost:8001 && start http://localhost:5678"
  %PY% scripts\dev.py
)
pause
