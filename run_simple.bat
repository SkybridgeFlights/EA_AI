@echo off
setlocal enableextensions enabledelayedexpansion
chcp 65001 >nul

:: ========= مسارات أساسية =========
cd /d "C:\EA_AI" || (echo [ERR] Project folder not found & pause & exit /b 1)
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
set "RUNTIME=%ROOT%\runtime"
set "LOGS=%ROOT%\logs"
set "TOOLS=%ROOT%\tools"
set "LIVECFG=%RUNTIME%\live_config.json"
set "MT5_COMMON=%APPDATA%\MetaQuotes\Terminal\Common\Files"
set "AI_SIG_DIR=%MT5_COMMON%\ai_signals"
set "PORT=8000"

:: ========= المتغيرات العامة =========
set "API_KEY=000"
set "BEST_RESULT_PATH=%ROOT%\artifacts\best_result.json"
set "TRADE_LOGS_DIR=%LOGS%"
set "LIVE_CONFIG_PATH=%LIVECFG%"
set "SELFCAL_INTERVAL_MIN=60"
set "PYTHONPATH=%ROOT%"

echo ============================================
echo   EA_AI - Simple Local Runner
echo   ROOT: %ROOT%
echo   PYTHON: %PY%
echo ============================================

echo.
echo [1] تجهيز المجلدات...
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if not exist "%LOGS%"    mkdir "%LOGS%"
if not exist "%AI_SIG_DIR%" mkdir "%AI_SIG_DIR%" 2>nul
if not exist "%LIVECFG%" (echo {}>"%LIVECFG%")

echo.
echo [2] إنشاء/فحص venv...
if not exist "%PY%" (
    py -3.11 -m venv "%VENV%" || (echo [ERR] venv creation failed & pause & exit /b 1)
)

echo.
echo [3] تثبيت المتطلبات...
"%PY%" -m pip install --upgrade pip wheel setuptools >nul 2>&1

if exist "%ROOT%\requirements.txt" (
    "%PIP%" install -r "%ROOT%\requirements.txt"
) else (
    "%PIP%" install uvicorn fastapi python-dotenv pandas numpy scikit-learn xgboost MetaTrader5 yfinance chardet hyperopt requests joblib >nul
)

echo.
echo [4] نشر live_config.json إلى MT5...
copy /Y "%LIVECFG%" "%MT5_COMMON%\live_config.json" >nul 2>&1

echo.
echo [5] إنشاء سكربتات المراقبة...
set "TMP=%TOOLS%\tmp"
if not exist "%TMP%" mkdir "%TMP%"

:: ---------- API ----------
(
echo @echo off
echo title EA_API
echo cd /d "%ROOT%"
echo echo [api] Starting Uvicorn...
echo :LOOP
echo "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload
echo echo [api] crashed ^(code %%errorlevel%%^) ... Restarting in 2s
echo timeout /t 2 ^>nul
echo goto LOOP
)>"%TMP%\_watch_api.bat"

:: ---------- SelfCal ----------
(
echo @echo off
echo title EA_SelfCal
echo cd /d "%ROOT%"
echo :LOOP
echo echo [selfcal] Running tools.selfcal_runner ...
echo "%PY%" -m tools.selfcal_runner
echo echo [selfcal] exited ^(%%errorlevel%%^) ... Restarting in 5s
echo timeout /t 5 ^>nul
echo goto LOOP
)>"%TMP%\_watch_selfcal.bat"

:: ---------- Writer ----------
(
echo @echo off
echo title AI_SignalWriter
echo cd /d "%ROOT%"
echo :LOOP
echo echo [writer] starting app.services.writer ...
echo "%PY%" -m app.services.writer
echo echo [writer] exited ^(%%errorlevel%%^) ... Restarting in 5s
echo timeout /t 5 ^>nul
echo goto LOOP
)>"%TMP%\_watch_writer.bat"

:: ---------- Sync Deals ----------
(
echo @echo off
echo title EA_DealsSync
echo cd /d "%ROOT%"
echo :LOOP
echo echo [sync_deals] Running tools.sync_deals_to_jsonl ...
echo "%PY%" -m tools.sync_deals_to_jsonl
echo echo [sync_deals] sleeping 60s...
echo timeout /t 60 ^>nul
echo goto LOOP
)>"%TMP%\_watch_sync_deals.bat"

echo.
echo [6] تشغيل الخدمات...

start "EA_API"         cmd /k "%TMP%\_watch_api.bat"
timeout /t 2 >nul
start "EA_SelfCal"     cmd /k "%TMP%\_watch_selfcal.bat"
start "AI_SignalWriter" cmd /k "%TMP%\_watch_writer.bat"
start "EA_DealsSync"   cmd /k "%TMP%\_watch_sync_deals.bat"

echo.
echo [7] فتح الواجهة...
start "" "http://127.0.0.1:%PORT%/dashboard"
start "" "http://127.0.0.1:%PORT%/docs"

echo.
echo ==========================================
echo   EA_AI Simple mode is running...
echo   Windows CMD windows now show full logs.
echo ==========================================
pause >nul