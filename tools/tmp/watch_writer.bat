@echo off
setlocal
cd /d "C:\EA_AI"
set "PYTHONPATH=C:\EA_AI"
set "API_KEY=000"
set "PREDICT_URL=http://127.0.0.1:8000"
set "LIVE_CONFIG_PATH=C:\EA_AI\runtime\live_config.json"
set "COMMON=%APPDATA%\MetaQuotes\Terminal\Common\Files"
set "AI_SIG_DIR=%APPDATA%\MetaQuotes\Terminal\Common\Files\ai_signals"
set "SYMBOLS=XAUUSD"
set "INTERVAL_SEC=1"
:LOOP
"C:\EA_AI\.venv\Scripts\python.exe" -X dev -m app.services.writer 1>>"C:\EA_AI\logs\writer.log" 2>&1
timeout /t 2 >nul
goto LOOP
