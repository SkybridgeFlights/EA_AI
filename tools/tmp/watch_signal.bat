@echo off
title EA_Signal
:LOOP
rem jitter عشوائي لتقليل التضارب
set /a _j=32005 % 8
timeout /t  /nobreak >nul
rem استدعاء API لتوليد الإشارة
powershell -NoProfile -Command "try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/signals/generate' -ContentType 'application/json' -Body (@{ force = $false } ^| ConvertTo-Json) ^| Out-Null } catch { }"
timeout /t 60 /nobreak >nul
goto LOOP
