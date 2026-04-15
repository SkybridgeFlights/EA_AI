@echo off
setlocal
title EA_Optimizer
set "PYTHONPATH=C:\EA_AI"
cd /d "C:\EA_AI"
:LOOP
echo [opt] starting tools/optimize_auto.py ...
"C:\EA_AI\.venv\Scripts\python.exe" "C:\EA_AI\tools\optimize_auto.py" --source mt5 --mt5_symbol XAUUSD --mt5_timeframe H1 --mt5_bars 100000 --outdir "C:\EA_AI\artifacts" --windows 3 --min_trades 12 --use_bayes 1 --tries 400 --expand 1 --max_expand 6 --timeout_min 20 --patience 3 --objective pf,wr,trades,dd --w_pf 0.8 --w_wr 0.3 --w_trades 1.2 --w_dd 0.00005 --seed 42 --jobs -1 --verbose 1 --daemon 0 --loops 1
echo [opt] finished with code %errorlevel%. Restarting in 6h...
timeout /t 21600 >nul
goto LOOP
