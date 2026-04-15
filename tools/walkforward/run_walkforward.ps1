param(
  [string]$Symbol = "XAUUSDr",
  [string]$Period = "M15",
  [int]$FoldStart = 0,
  [int]$FoldEnd   = 46,

  # هذه هي الفولدر الذي يحتوي folds عندك (كما ظهر: ...\wf_...\fold_001\signals_...csv)
  [string]$WFRoot = "C:\EA_AI\reports\walk_forward",

  # مسارات MT5
  [string]$TerminalRoot = "$env:APPDATA\MetaQuotes\Terminal",
  [string]$Hash = "D0E8209F77C8CF37AD8BF550E51FF075",

  # template ini
  [string]$TemplateIni = "C:\EA_AI\tools\walkforward\mt5_tester_template.ini",

  # أين نحفظ تقارير الـ folds
  [string]$OutDir = "C:\EA_AI\reports\walk_forward_runs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$mt5exe = Join-Path $TerminalRoot "$Hash\terminal64.exe"
if(!(Test-Path $mt5exe)){ throw "MT5 exe not found: $mt5exe" }

if(!(Test-Path $TemplateIni)){ throw "Template INI not found: $TemplateIni" }

New-Item -ItemType Directory -Force $OutDir | Out-Null

# مكان ملفات الإشارات التي يقرأها MT5 أثناء الباكتيست
$dstCommon = Join-Path $TerminalRoot "Common\Files\ai_signals"
New-Item -ItemType Directory -Force $dstCommon | Out-Null

function Find-FoldSignalsFile([string]$fold3){
  $fname = "signals_${Symbol}_${Period}_fold$fold3.csv"
  $found = Get-ChildItem -Path $WFRoot -Recurse -File -Filter $fname -ErrorAction SilentlyContinue | Select-Object -First 1
  if(!$found){ return $null }
  return $found.FullName
}

function Stop-MT5(){
  # أحياناً الملف يكون مقفول لأن التستر يعمل/Agent
  Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 600
}

for($i=$FoldStart; $i -le $FoldEnd; $i++){
  $fold3 = "{0:D3}" -f $i
  $sigPath = Find-FoldSignalsFile $fold3
  if(!$sigPath){
    Write-Host "SKIP fold $fold3 (signals not found under $WFRoot)"
    continue
  }

  $sigName = "signals_${Symbol}_${Period}_fold$fold3.csv"
  $sigDst  = Join-Path $dstCommon $sigName

  # تأكد لا يوجد MT5 شغال يقفل الملف
  Stop-MT5

  Copy-Item $sigPath $sigDst -Force

  # نحتاج ملف .set خاص بهذا الـ fold (نعدل فيه قيمة BT_AISignalsCSV)
  # إذا عندك ملف set base جاهز من التستر، ضع مساره هنا:
  $baseSet = "C:\EA_AI\102.set"   # عدّل إن كان مختلفاً
  if(!(Test-Path $baseSet)){ throw "Base .set not found: $baseSet" }

  $foldSetDir = Join-Path $OutDir "fold_$fold3"
  New-Item -ItemType Directory -Force $foldSetDir | Out-Null
  $foldSet = Join-Path $foldSetDir "inputs_fold$fold3.set"

  Copy-Item $baseSet $foldSet -Force

  # عدّل السطر الخاص بالـ CSV داخل ملف .set (صيغة MT5: key=value)
  # نضع مساراً نسبياً كما يظهر عندك: ai_signals\signals_...csv
  $rel = "ai_signals\$sigName"
  $content = Get-Content $foldSet -Raw

  if($content -match "(?m)^BT_AISignalsCSV=.*$"){
    $content = [regex]::Replace($content, "(?m)^BT_AISignalsCSV=.*$", "BT_AISignalsCSV=$rel")
  } else {
    $content = $content.TrimEnd() + "`r`nBT_AISignalsCSV=$rel`r`n"
  }

  Set-Content -Path $foldSet -Value $content -Encoding ASCII

  # أنشئ ini خاص بهذا الـ fold
  $foldIni = Join-Path $foldSetDir "tester_fold$fold3.ini"
  $reportHtml = Join-Path $foldSetDir "Report_fold$fold3.html"

  $ini = Get-Content $TemplateIni -Raw
  $ini = $ini -replace "(?m)^Report=.*$", "Report=$reportHtml"
  $ini = $ini -replace "(?m)^ExpertParameters=.*$", "ExpertParameters=$foldSet"
  Set-Content -Path $foldIni -Value $ini -Encoding ASCII

  Write-Host "RUN fold $fold3"
  & $mt5exe "/config:$foldIni" | Out-Null

  if(Test-Path $reportHtml){
    Write-Host "OK  fold $fold3 -> $reportHtml"
  } else {
    Write-Host "WARN fold $fold3: report not found (check Journal/Agent)."
  }
}

Write-Host "DONE. Reports in: $OutDir"
