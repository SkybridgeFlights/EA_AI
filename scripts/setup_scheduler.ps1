# scripts/setup_scheduler.ps1
# ================================================================
# EA_AI Windows Task Scheduler Setup
# ================================================================
# Tasks:
#   EA_AI_Regime        every 1 hour    - regime_classifier --classify-last
#   EA_AI_SelfCal       every 15 min    - selfcal_runner --once
#   EA_AI_Pipeline      daily  00:00   - run_pipeline --skip-train --promote
#   EA_AI_WeeklyTrain   sunday 02:00   - run_pipeline --promote (full train)
#
# Run (as Administrator or current user):
#   cd C:\EA_AI
#   .\scripts\setup_scheduler.ps1
#
# Uninstall all tasks:
#   .\scripts\setup_scheduler.ps1 -Uninstall
# ================================================================

param(
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── Paths ───────────────────────────────────────────────────────
$ROOT    = "C:\EA_AI"
$VENV_PY = Join-Path $ROOT ".venv\Scripts\python.exe"

if (Test-Path $VENV_PY) {
    $PYTHON = $VENV_PY
} else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Error "Python not found. Ensure .venv is set up or python is on PATH."
        exit 1
    }
    $PYTHON = $found.Source
}

$LOG_DIR     = Join-Path $ROOT "logs\scheduler"
$TASK_FOLDER = "EA_AI"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  EA_AI Task Scheduler Setup"                    -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  ROOT   : $ROOT"
Write-Host "  PYTHON : $PYTHON"
Write-Host "  LOGS   : $LOG_DIR"
Write-Host ""

# ─── Task definitions ────────────────────────────────────────────
# Each entry: Name, Description, Args, LogFile, TriggerType, StartTime(optional)
$Tasks = @(
    [ordered]@{
        Name        = "EA_AI_Regime"
        Description = "Regime Classifier - every 1 hour"
        Args        = "-m tools.regime_classifier --classify-last"
        LogFile     = "regime.log"
        TriggerType = "Hourly"
        StartTime   = "00:05"
    },
    [ordered]@{
        Name        = "EA_AI_SelfCal"
        Description = "SelfCal Runner - every 15 minutes"
        Args        = "-m tools.selfcal_runner --once"
        LogFile     = "selfcal.log"
        TriggerType = "Every15Min"
        StartTime   = "00:00"
    },
    [ordered]@{
        Name        = "EA_AI_Pipeline"
        Description = "Daily Pipeline - 00:00 skip-train"
        Args        = "-m tools.run_pipeline --skip-train --promote"
        LogFile     = "pipeline_daily.log"
        TriggerType = "Daily"
        StartTime   = "00:00"
    },
    [ordered]@{
        Name        = "EA_AI_WeeklyTrain"
        Description = "Weekly Full Pipeline - Sunday 02:00"
        Args        = "-m tools.run_pipeline --promote"
        LogFile     = "pipeline_weekly.log"
        TriggerType = "WeeklySunday"
        StartTime   = "02:00"
    }
)

# ─── Helper: remove task if exists ───────────────────────────────
function Remove-TaskIfExists {
    param([string]$TaskName)
    $existing = Get-ScheduledTask -TaskName $TaskName `
                    -TaskPath "\$TASK_FOLDER\" -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName `
            -TaskPath "\$TASK_FOLDER\" -Confirm:$false
        Write-Host "  [removed] $TaskName" -ForegroundColor Yellow
    }
}

# ─── Uninstall mode ──────────────────────────────────────────────
if ($Uninstall) {
    Write-Host "Uninstalling all EA_AI scheduled tasks..." -ForegroundColor Yellow
    foreach ($t in $Tasks) { Remove-TaskIfExists $t.Name }
    Write-Host "Done." -ForegroundColor Green
    exit 0
}

# ─── Create log directory ────────────────────────────────────────
if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
    Write-Host "  [created] $LOG_DIR"
}

# ─── Create task folder in Task Scheduler ────────────────────────
try {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    try { $svc.GetFolder("\").CreateFolder($TASK_FOLDER) | Out-Null }
    catch { <# already exists #> }
} catch {
    Write-Warning "Could not create scheduler folder: $_"
}

# ─── Action builder ──────────────────────────────────────────────
function New-PythonAction {
    param([string]$PyArgs, [string]$LogFile)
    $logPath = Join-Path $LOG_DIR $LogFile
    # cmd /c: redirects stdout+stderr to log file
    $cmdArg  = "/c `"cd /d `"$ROOT`" && `"$PYTHON`" $PyArgs >> `"$logPath`" 2>&1`""
    return New-ScheduledTaskAction `
        -Execute         "cmd.exe" `
        -Argument        $cmdArg `
        -WorkingDirectory $ROOT
}

# ─── Trigger builder ─────────────────────────────────────────────
function New-EATrigger {
    param([string]$TriggerType, [string]$StartTime)

    $parts = $StartTime.Split(":")
    $h = [int]$parts[0]
    $m = [int]$parts[1]
    $atTime = (Get-Date).Date.AddHours($h).AddMinutes($m)

    switch ($TriggerType) {
        "Hourly" {
            $t = New-ScheduledTaskTrigger -Once -At $atTime `
                     -RepetitionInterval  (New-TimeSpan -Hours 1) `
                     -RepetitionDuration  (New-TimeSpan -Days 9999)
            return $t
        }
        "Every15Min" {
            $t = New-ScheduledTaskTrigger -Once -At $atTime `
                     -RepetitionInterval  (New-TimeSpan -Minutes 15) `
                     -RepetitionDuration  (New-TimeSpan -Days 9999)
            return $t
        }
        "Daily" {
            return New-ScheduledTaskTrigger -Daily -At $atTime
        }
        "WeeklySunday" {
            return New-ScheduledTaskTrigger `
                       -Weekly -WeeksInterval 1 `
                       -DaysOfWeek Sunday `
                       -At $atTime
        }
        default {
            throw "Unknown TriggerType: $TriggerType"
        }
    }
}

# ─── Shared settings ─────────────────────────────────────────────
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit          (New-TimeSpan -Hours 2) `
    -MultipleInstances           IgnoreNew `
    -RestartCount                2 `
    -RestartInterval             (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -WakeToRun:$false

# ─── Principal (run as current user) ─────────────────────────────
$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERDOMAIN\$env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Highest

# ─── Register tasks ──────────────────────────────────────────────
Write-Host "Registering tasks..." -ForegroundColor Cyan
Write-Host ""

$results = @()

foreach ($t in $Tasks) {
    try {
        Remove-TaskIfExists $t.Name

        $action  = New-PythonAction $t.Args $t.LogFile
        $trigger = New-EATrigger    $t.TriggerType $t.StartTime

        Register-ScheduledTask `
            -TaskName    $t.Name `
            -TaskPath    "\$TASK_FOLDER\" `
            -Action      $action `
            -Trigger     $trigger `
            -Settings    $settings `
            -Principal   $principal `
            -Description $t.Description `
            -Force | Out-Null

        $results += [PSCustomObject]@{
            Task    = $t.Name
            Trigger = $t.TriggerType
            Start   = $t.StartTime
            Log     = $t.LogFile
            Status  = "OK"
        }
        Write-Host "  [OK]   $($t.Name)  ($($t.TriggerType))" -ForegroundColor Green

    } catch {
        $results += [PSCustomObject]@{
            Task    = $t.Name
            Trigger = $t.TriggerType
            Start   = $t.StartTime
            Log     = $t.LogFile
            Status  = "FAILED"
        }
        Write-Host "  [FAIL] $($t.Name): $_" -ForegroundColor Red
    }
}

# ─── Summary ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Summary"                                         -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
$results | Format-Table Task, Trigger, Start, Status -AutoSize

Write-Host "Log directory : $LOG_DIR" -ForegroundColor Gray
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Gray
Write-Host "  Open Task Scheduler  : taskschd.msc"
Write-Host "  Run Regime now       : Start-ScheduledTask -TaskPath '\EA_AI\' -TaskName 'EA_AI_Regime'"
Write-Host "  Run SelfCal now      : Start-ScheduledTask -TaskPath '\EA_AI\' -TaskName 'EA_AI_SelfCal'"
Write-Host "  Run Pipeline now     : Start-ScheduledTask -TaskPath '\EA_AI\' -TaskName 'EA_AI_Pipeline'"
Write-Host "  Uninstall all        : .\scripts\setup_scheduler.ps1 -Uninstall"
Write-Host ""

$failedList = @($results | Where-Object { $_.Status -ne "OK" })
$failed = $failedList.Count
if ($failed -eq 0) {
    Write-Host "All $($Tasks.Count) tasks registered successfully." -ForegroundColor Green
} else {
    Write-Host "$failed task(s) failed. Check errors above." -ForegroundColor Red
    exit 1
}
