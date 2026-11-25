@echo off
setlocal
cd /d "%~dp0"
python -c "import OpenRTM_aist, RTC" >nul 2>&1
if errorlevel 1 (
  echo OpenRTM-aist 2.1 Python edition is required. See README.md.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0.deps;%PYTHONPATH%"
python -c "import websockets.sync.server, serial" >nul 2>&1
if errorlevel 1 (
  python -m pip install --no-index --find-links wheelhouse --target .deps -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
set "WATCHRTM_TOKEN="
start "WatchRTM Demo Data" /b python scripts\demo.py --wait --open-browser
python -m watchrtm --config config.demo.json
if errorlevel 1 pause
