Param(
  [string]$Base = "http://127.0.0.1:8000",
  [string]$Symbol = $env:SYMBOL,
  [int]$PeriodSec = 45,
  [bool]$Force = $true
)

$ErrorActionPreference = "Stop"

function Wait-ApiReady {
  param([string]$u)
  for ($i=0; $i -lt 120; $i++) {
    try {
      $r = Invoke-RestMethod -Method GET -Uri "$u/healthz" -TimeoutSec 4
      if ($r) { return $true }
    } catch { }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Gen-Signal {
  param([string]$u,[string]$sym,[bool]$force)
  try {
    $body = @{ symbol = $sym; force = $force } | ConvertTo-Json
    Invoke-RestMethod -Method POST -Uri "$u/signals/generate" -ContentType "application/json" -Body $body | Out-Null
  } catch {
    Write-Host "[SIG][ERR] $($_.Exception.Message)"
  }
}

if (-not (Wait-ApiReady -u $Base)) {
  Write-Host "[SIG] API not ready"
  Start-Sleep -Seconds 3
  exit 1
}

Write-Host "[SIG] started -> $Base  symbol=$Symbol  period=${PeriodSec}s"
while ($true) {
  Gen-Signal -u $Base -sym $Symbol -force $Force
  Start-Sleep -Seconds $PeriodSec
}
