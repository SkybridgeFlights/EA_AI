@echo off
cd /d "C:\EA_AI"
:LOOP
echo [%time%] [sync_deals] syncing...
"C:\EA_AI\.venv\Scripts\python.exe" -m tools.sync_deals_to_jsonl >> "C:\EA_AI\logs\scheduler\sync.log" 2>&1
echo [%time%] [sync_deals] done. next in 60s...
timeout /t 60 >nul
goto LOOP
