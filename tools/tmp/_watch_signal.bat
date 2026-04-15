@echo off
setlocal
title AI_SignalGen
set "PYTHONPATH=C:\EA_AI"
cd /d "C:\EA_AI"
:LOOP
echo [writer] starting app.services.writer ...
"C:\EA_AI\.venv\Scripts\python.exe" -m app.services.writer
echo [writer] exited with code %errorlevel%. Restarting in 5s...
timeout /t 5 >nul
goto LOOP
