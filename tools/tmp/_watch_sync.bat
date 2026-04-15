@echo off
setlocal
set "AI_SIG_SRC=C:\EA_AI\ai_signals\xauusdr_signal.ini"
set "AI_SIG_DST=%APPDATA%\MetaQuotes\Terminal\Common\Files\ai_signals\xauusdr_signal.ini"
:LOOP
"C:\EA_AI\.venv\Scripts\python.exe" "C:\EA_AI\tools\sync_ai_signals.py" > "C:\EA_AI\sync_signal_log.txt" 2>&1
echo [sync] stopped - restarting in 2s...
ping -n 3 127.0.0.1 >nul
goto LOOP
