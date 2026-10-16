@echo off
REM Order Exception Agent - one-click demo for Windows.
REM Installs Python packages (first run only takes a minute), starts the backend, opens the dashboard.
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
echo Starting... the dashboard opens in your browser in a few seconds.
start "" cmd /c "timeout /t 7 >nul & start http://localhost:8001"
%PY% scripts\dev.py
pause
