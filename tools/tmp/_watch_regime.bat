@echo off
cd /d "C:\EA_AI"
:LOOP
echo [%time%] [regime] classifying...
"C:\EA_AI\.venv\Scripts\python.exe" -m tools.regime_classifier --classify-last >> "C:\EA_AI\logs\scheduler\regime.log" 2>&1
echo [%time%] [regime] done. next in 3600s...
timeout /t 3600 >nul
goto LOOP
