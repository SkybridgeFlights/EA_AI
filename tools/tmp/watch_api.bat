@echo off
setlocal
cd /d "C:\EA_AI"
:LOOP
"C:\EA_AI\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload 1>>"C:\EA_AI\logs\server.log" 2>&1
timeout /t 2 >nul
goto LOOP
