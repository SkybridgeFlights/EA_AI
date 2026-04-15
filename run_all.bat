@echo off
setlocal enableextensions enabledelayedexpansion

:: ========= Paths =========
cd /d "C:\EA_AI" || (echo [ERR] Project folder not found ^& pause ^& exit /b 1)
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
set "RUNTIME=%ROOT%\runtime"
set "LOGS=%ROOT%\logs"
set "SCHED_LOGS=%LOGS%\scheduler"
set "TOOLS=%ROOT%\tools"
set "LIVECFG=%RUNTIME%\live_config.json"
set "MT5_COMMON=%APPDATA%\MetaQuotes\Terminal\Common\Files"
set "AI_SIG_DIR=%MT5_COMMON%\ai_signals"
set "PORT=8000"

:: ========= Env =========
set "API_KEY=000"
set "TRADE_LOGS_DIR=%LOGS%"
set "LIVE_CONFIG_PATH=%LIVECFG%"
set "PYTHONPATH=%ROOT%"

:: ========= [1] Folders =========
echo [1] Creating folders...
if not exist "%RUNTIME%"    mkdir "%RUNTIME%"
if not exist "%LOGS%"       mkdir "%LOGS%"
if not exist "%SCHED_LOGS%" mkdir "%SCHED_LOGS%"
if not exist "%TOOLS%"      mkdir "%TOOLS%"
if not exist "%AI_SIG_DIR%" mkdir "%AI_SIG_DIR%"
if not exist "%LIVECFG%"    echo {}>"%LIVECFG%"

:: ========= [2] Venv =========
echo [2] Checking venv Python 3.12...
if not exist "%PY%" (
  py -3.12 -m venv "%VENV%" || (echo [ERR] venv failed ^& pause ^& exit /b 1)
)

:: ========= [3] Requirements =========
echo [3] Installing requirements...
"%PY%" -m pip install --upgrade pip wheel setuptools
if exist "%ROOT%\requirements.txt" (
  "%PIP%" install -r "%ROOT%\requirements.txt"
) else (
  "%PIP%" install uvicorn fastapi python-dotenv pandas numpy xgboost yfinance requests joblib
)

:: ========= [4] Deploy live_config =========
echo [4] Deploying live_config...
copy /Y "%LIVECFG%" "%MT5_COMMON%\live_config.json"

:: ========= [5] Watcher scripts =========
echo [5] Building watcher scripts...
set "TMPRUN=%TOOLS%\tmp"
if not exist "%TMPRUN%" mkdir "%TMPRUN%"

:: --- API ---
(
  echo @echo off
  echo cd /d "%ROOT%"
  echo :LOOP
  echo echo [api] starting uvicorn...
  echo "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
  echo echo [api] stopped. waiting 5s...
  echo timeout /t 5 ^>nul
  echo goto LOOP
) > "%TMPRUN%\_watch_api.bat"

:: --- SelfCal every 900s ---
(
  echo @echo off
  echo cd /d "%ROOT%"
  echo :LOOP
  echo echo [%%time%%] [selfcal] running...
  echo "%PY%" -m tools.selfcal_runner --once ^>^> "%SCHED_LOGS%\selfcal.log" 2^>^&1
  echo echo [%%time%%] [selfcal] done. next in 900s...
  echo timeout /t 900 ^>nul
  echo goto LOOP
) > "%TMPRUN%\_watch_selfcal.bat"

:: --- Regime every 3600s ---
(
  echo @echo off
  echo cd /d "%ROOT%"
  echo :LOOP
  echo echo [%%time%%] [regime] classifying...
  echo "%PY%" -m tools.regime_classifier --classify-last ^>^> "%SCHED_LOGS%\regime.log" 2^>^&1
  echo echo [%%time%%] [regime] done. next in 3600s...
  echo timeout /t 3600 ^>nul
  echo goto LOOP
) > "%TMPRUN%\_watch_regime.bat"

:: --- Sync Deals every 60s ---
(
  echo @echo off
  echo cd /d "%ROOT%"
  echo :LOOP
  echo echo [%%time%%] [sync_deals] syncing...
  echo "%PY%" -m tools.sync_deals_to_jsonl ^>^> "%SCHED_LOGS%\sync.log" 2^>^&1
  echo echo [%%time%%] [sync_deals] done. next in 60s...
  echo timeout /t 60 ^>nul
  echo goto LOOP
) > "%TMPRUN%\_watch_sync_deals.bat"

:: ========= [6] Kill existing port 8000 =========
echo [6] Stopping any existing process on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    echo [kill] PID %%a on port 8000
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 >nul

:: ========= [7] Start services =========
echo [7] Starting services...
start "EA_API"     cmd /k "%TMPRUN%\_watch_api.bat"
timeout /t 3
start "EA_SelfCal" cmd /k "%TMPRUN%\_watch_selfcal.bat"
start "EA_Regime"  cmd /k "%TMPRUN%\_watch_regime.bat"
start "EA_Sync"    cmd /k "%TMPRUN%\_watch_sync_deals.bat"
timeout /t 3

:: ========= [8] Open browser =========
echo [8] Opening browser...
start "" "http://127.0.0.1:%PORT%/dashboard"
start "" "http://127.0.0.1:%PORT%/docs"
start "" notepad.exe "%LIVECFG%"

echo.
echo ===================================================
echo  EA_AI Running
echo  Logs: %SCHED_LOGS%
echo    api.log     - uvicorn
echo    selfcal.log - every 15 min
echo    regime.log  - every 60 min
echo    sync.log    - every 60 sec
echo ===================================================
pause
