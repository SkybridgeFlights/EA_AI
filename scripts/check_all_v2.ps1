#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8000'

function Section($t){ "`n===========================================`n[$t]`n===========================================" }
function OK($m){ "OK: $m" }
function FAIL($m){ "FAIL: $m" }

# [1] Processes
Section '1) Processes'
try{
  Get-Process python,uvicorn -ErrorAction SilentlyContinue |
    Select-Object Id,ProcessName,Path,StartTime -ErrorAction SilentlyContinue |
    Format-Table -AutoSize | Out-Host
  if(-not (Get-Process uvicorn -ErrorAction SilentlyContinue)){ 'Note: no uvicorn process.' | Out-Host }
}catch{ $_ | Out-Host }

# [2] / and /healthz
Section '2) / and /healthz'
try{
  $h = Invoke-RestMethod -Uri ($base + '/healthz') -Method GET
  OK "/healthz uptime=$($h.uptime_sec)"
  $r = Invoke-RestMethod -Uri $base -Method GET
  OK "root status=$($r.status) dashboard_bound=$($r.dashboard_bound)"
}catch{
  FAIL "healthz/root: $($_.Exception.Message)"
}

# [3] dashboard
Section '3) dashboard'
try{
  $html = Invoke-WebRequest -Uri ($base + '/dashboard') -UseBasicParsing
  if($html.StatusCode -eq 200){ OK '/dashboard' } else { FAIL "/dashboard code=$($html.StatusCode)" }
}catch{ FAIL "/dashboard: $($_.Exception.Message)" }

# [4] SelfCal once + live_config files
Section '4) SelfCal once + live_config'
try{
  $res = Invoke-RestMethod -Uri ($base + '/api/selfcal/once?shadow=false') -Method POST
  if($res -and $res.ok){ OK 'selfcal/once ok' } else { FAIL 'selfcal/once returned bad response' }
}catch{ FAIL "selfcal/once: $($_.Exception.Message)" }

$runtimeLive = 'C:\EA_AI\runtime\live_config.json'
$commonLive  = "$env:APPDATA\MetaQuotes\Terminal\Common\Files\live_config.json"

try{
  if(Test-Path $runtimeLive){
    Get-Item $runtimeLive | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize | Out-Host
  } else { 'Runtime live_config.json missing' | Out-Host }
  if(Test-Path $commonLive){
    Get-Item $commonLive | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize | Out-Host
  } else { 'Common live_config.json missing' | Out-Host }
}catch{ $_ | Out-Host }

# [5] Signal: generate + read INI
Section '5) Signal generate + read INI'
try{
  $payload = @{ symbol='XAUUSD'; force=$true }
  $sig = Invoke-RestMethod -Uri ($base + '/signals/generate') -Method POST -Body ($payload | ConvertTo-Json) -ContentType 'application/json'
  if($sig){ OK '/signals/generate returned data' } else { FAIL '/signals/generate returned nothing' }

  $iniPath = "$env:APPDATA\MetaQuotes\Terminal\Common\Files\ai_signals\xauusd_signal.ini"
  if(Test-Path $iniPath){
    'OK: signal INI found:' | Out-Host
    (Get-Content $iniPath -Encoding UTF8) | ForEach-Object {
      if($_ -match '^(ts|symbol|direction|confidence|hold_minutes|rr|risk_pct)='){
        $_ | Out-Host
      }
    }
    Get-Item $iniPath | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize | Out-Host
  } else {
    FAIL "signal INI not found: $iniPath"
  }
}catch{ FAIL "signal section: $($_.Exception.Message)" }

# [6] /api/dashboard/* endpoints
Section '6) /api/dashboard/*'
try{
  $dbg = Invoke-RestMethod -Uri ($base + '/api/dashboard/debug') -Method GET
  if($dbg){ OK '/api/dashboard/debug' } else { FAIL '/api/dashboard/debug' }

  $tr = Invoke-RestMethod -Uri ($base + '/api/dashboard/test_read') -Method GET
  'test_read keys: ' + (($tr.Keys | Select-Object -First 3) -join ', ') | Out-Host

  $sm = Invoke-RestMethod -Uri ($base + '/api/dashboard/summary?last_n=50&page=1&page_size=25') -Method GET
  if($sm){ OK '/api/dashboard/summary' } else { FAIL '/api/dashboard/summary' }

  try{
    $lr = Invoke-RestMethod -Uri ($base + '/api/dashboard/last_report') -Method GET
    if($lr){ OK '/api/dashboard/last_report' } else { 'last_report not available' | Out-Host }
  }catch{ 'last_report not available' | Out-Host }
}catch{ FAIL "endpoints: $($_.Exception.Message)" }

# [7] .env syntax check
Section '7) .env syntax check'
try{
  $envFile = 'C:\EA_AI\.env'
  if(Test-Path $envFile){
    $lines = Get-Content $envFile -Encoding UTF8
    $bad = @()
    for($i=0; $i -lt $lines.Count; $i++){
      $ln = $lines[$i]
      if($ln -match '^\s*$'){ continue }
      if($ln -match '^\s*#'){ continue }
      if($ln -notmatch '^[A-Za-z_][A-Za-z0-9_]*='){
        $bad += [PSCustomObject]@{ line=$i+1; text=$ln }
      }
    }
    if($bad.Count -gt 0){
      'ENV BAD LINES:' | Out-Host
      $bad | Format-Table line,text -AutoSize | Out-Host
    } else { OK '.env syntax' }
  } else {
    '.env file not found' | Out-Host
  }
}catch{ FAIL ".env: $($_.Exception.Message)" }

# [8] MT5 Common files
Section '8) MT5 Common files'
try{
  $d = "$env:APPDATA\MetaQuotes\Terminal\Common\Files"
  if(Test-Path $d){
    Get-ChildItem $d -File |
      Select-Object Name,Length,LastWriteTime |
      Format-Table -AutoSize | Out-Host
    OK 'MT5 Common listing'
  } else { 'Common folder not found' | Out-Host }
}catch{ FAIL "MT5 files: $($_.Exception.Message)" }

"`n===== check_all_v2 finished =====" | Out-Host

