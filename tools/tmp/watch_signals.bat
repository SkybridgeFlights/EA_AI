@echo off
cd /d "C:\EA_AI"
:LOOP
"C:\EA_AI\.venv\Scripts\python.exe" -X dev -m app.services.signal_daemon 1>>"C:\EA_AI\logs\signals.log" 2>&1
timeout /t 2 >nul
goto LOOP
