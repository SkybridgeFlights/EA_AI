@echo off
cd /d "C:\EA_AI"
:LOOP
echo [api] starting uvicorn...
"C:\EA_AI\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo [api] stopped. waiting 5s...
timeout /t 5 >nul
goto LOOP
