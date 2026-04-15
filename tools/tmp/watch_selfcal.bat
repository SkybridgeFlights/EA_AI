@echo off
setlocal
cd /d "C:\EA_AI"
set "TRADE_LOGS_DIR=C:\EA_AI\logs"
set "LIVE_CONFIG_PATH=C:\EA_AI\runtime\live_config.json"
set "SELFCAL_INTERVAL_MIN=60"
:LOOP
"C:\EA_AI\.venv\Scripts\python.exe" "C:\EA_AI\tools\selfcal_runner.py" 1>>"C:\EA_AI\logs\selfcal.log" 2>&1
timeout /t 2 >nul
goto LOOP
