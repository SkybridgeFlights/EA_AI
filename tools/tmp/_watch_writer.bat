@echo off
title AI_SignalWriter
cd /d "C:\EA_AI"
:LOOP
echo [writer] starting app.services.writer ...
"C:\EA_AI\.venv\Scripts\python.exe" -m app.services.writer
echo [writer] exited (%errorlevel%) ... Restarting in 5s
timeout /t 5 >nul
goto LOOP
