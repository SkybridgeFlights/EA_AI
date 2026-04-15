@echo off
cd /d "C:\EA_AI"
:LOOP
echo [%time%] [selfcal] running...
"C:\EA_AI\.venv\Scripts\python.exe" -m tools.selfcal_runner --once >> "C:\EA_AI\logs\scheduler\selfcal.log" 2>&1
echo [%time%] [selfcal] done. next in 900s...
timeout /t 900 >nul
goto LOOP
