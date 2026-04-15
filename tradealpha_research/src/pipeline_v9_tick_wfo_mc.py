# src/pipeline_v9_tick_wfo_mc.py
from __future__ import annotations

import os
import re
import json
import time
import glob
import subprocess
from pathlib import Path
from datetime import datetime


# =========================
# USER CONFIG (EDIT IF NEEDED)
# =========================
MT5_TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"
BASE_INI     = r"configs\tick_base.ini"

# اسم الاكسبرت كما في Strategy Tester (لازم يكون موجود في MT5 Data Folder ضمن MQL5\Experts)
EXPERT_NAME  = r"EA V8.ex5"

# الرمز/الفريم
SYMBOL       = "XAUUSDr"
PERIOD       = "H1"
FROM_DATE    = "2021.01.01"
TO_DATE      = "2025.12.31"

# ملفات التشغيل
RUN_SET_REL  = r"configs\_RUN_CURRENT.set"
OUT_ROOT     = Path("out_v9_tick")


# =========================
# HELPERS
# =========================
def _now_ts() -> float:
    return time.time()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_set_utf16le(params: dict, set_path: str) -> None:
    """
    Writes MT5 .set file in UTF-16LE with BOM.
    Format: Key=Value per line.
    """
    lines = []
    for k, v in params.items():
        if isinstance(v, bool):
            vv = "true" if v else "false"
        else:
            vv = str(v)
        lines.append(f"{k}={vv}")

    txt = "\n".join(lines) + "\n"
    p = Path(set_path)
    _ensure_dir(p.parent)

    # UTF-16LE with BOM
    p.write_bytes(b"\xff\xfe" + txt.encode("utf-16le"))


def patch_ini(
    base_ini: str,
    out_ini: str,
    *,
    expert_name: str,
    symbol: str,
    period: str,
    from_date: str,
    to_date: str,
    report_prefix_abs: str,
    expert_params_abs: str,
) -> None:
    """
    Patch a base MT5 tester ini to a runnable ini:
      - Expert, Symbol, Period, FromDate, ToDate, Report, ExpertParameters
      - ReplaceReport=1, ShutdownTerminal=1
    """
    src = Path(base_ini)
    if not src.exists():
        raise FileNotFoundError(f"Base INI not found: {src.resolve()}")

    lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()

    def set_kv(line: str, key: str, val: str) -> str:
        if line.strip().lower().startswith(key.lower() + "="):
            return f"{key}={val}"
        return line

    out_lines = []
    in_tester = False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_tester = (s.lower() == "[tester]")
            out_lines.append(line)
            continue

        if in_tester:
            line = set_kv(line, "Expert", expert_name)
            line = set_kv(line, "Symbol", symbol)
            line = set_kv(line, "Period", period)
            line = set_kv(line, "FromDate", from_date)
            line = set_kv(line, "ToDate", to_date)

            # report prefix: MT5 will create report.htm / report.html (and sometimes report.xml)
            line = set_kv(line, "Report", report_prefix_abs)
            line = set_kv(line, "ReplaceReport", "1")
            line = set_kv(line, "ShutdownTerminal", "1")

            # IMPORTANT: absolute path to .set
            line = set_kv(line, "ExpertParameters", expert_params_abs)

        out_lines.append(line)

    outp = Path(out_ini)
    _ensure_dir(outp.parent)
    outp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def run_mt5_terminal(mt5_terminal: str, ini_path: str, log_path: str, timeout_sec: int = 6 * 60 * 60) -> tuple[int, float]:
    terminal = Path(mt5_terminal)
    ini = Path(ini_path)
    if not terminal.exists():
        raise FileNotFoundError(f"MT5 terminal not found: {terminal}")
    if not ini.exists():
        raise FileNotFoundError(f"INI not found: {ini}")

    cmd = [str(terminal), f"/config:{str(ini)}"]
    t0 = _now_ts()

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        raise RuntimeError(f"MT5 timeout after {timeout_sec} sec. ini={ini}")

    elapsed = _now_ts() - t0

    lp = Path(log_path)
    _ensure_dir(lp.parent)
    lp.write_text(
        "CMD:\n" + " ".join(cmd) + "\n\nSTDOUT:\n" + (out or "") + "\n\nSTDERR:\n" + (err or "") + "\n",
        encoding="utf-8",
        errors="ignore",
    )

    return p.returncode, elapsed


def parse_mt5_report(html_path: str) -> dict:
    """
    Parses a MT5 HTML report for basic metrics.
    """
    p = Path(html_path)
    if not p.exists():
        raise FileNotFoundError(f"Report not found: {p}")

    txt = p.read_text(encoding="utf-8", errors="ignore")

    def find_float(pattern: str):
        m = re.search(pattern, txt, re.IGNORECASE)
        if not m:
            return None
        val = m.group(1).replace("\xa0", "").replace(" ", "").replace(",", "")
        try:
            return float(val)
        except:
            return None

    net_profit = find_float(r"Total Net Profit<\/td>\s*<td[^>]*>\s*([-\d\.,]+)")
    pf        = find_float(r"Profit Factor<\/td>\s*<td[^>]*>\s*([-\d\.,]+)")
    dd        = find_float(r"Drawdown Relative<\/td>\s*<td[^>]*>\s*([-\d\.,]+)")
    trades    = find_float(r"Total Trades<\/td>\s*<td[^>]*>\s*([-\d\.,]+)")

    return {
        "net_profit": net_profit,
        "profit_factor": pf,
        "max_dd_pct": dd,
        "trades": trades,
        "report": str(p.resolve()),
    }


def _candidate_terminal_roots() -> list[Path]:
    """
    Typical MT5 terminal data roots.
    We scan both Roaming and Local AppData.
    """
    roots = []
    appdata = os.environ.get("APPDATA", "")
    localapp = os.environ.get("LOCALAPPDATA", "")

    if appdata:
        roots.append(Path(appdata) / "MetaQuotes" / "Terminal")
    if localapp:
        roots.append(Path(localapp) / "MetaQuotes" / "Terminal")

    # Keep only existing
    out = []
    for r in roots:
        if r.exists():
            out.append(r)
    return out


def _find_reports_under_terminal(after_ts: float) -> list[Path]:
    """
    Find report*.htm/html under MT5 terminal folders modified after after_ts.
    """
    found: list[Path] = []
    for root in _candidate_terminal_roots():
        # Common locations:
        # 1) ...\Terminal\<HASH>\TesterReports\report*.htm*
        # 2) ...\Terminal\<HASH>\Tester\*\report*.htm*
        # 3) ...\Terminal\<HASH>\Tester\reports\report*.htm*
        patterns = [
            str(root / "*" / "TesterReports" / "report*.htm*"),
            str(root / "*" / "Tester" / "*" / "report*.htm*"),
            str(root / "*" / "Tester" / "reports" / "report*.htm*"),
            str(root / "*" / "Tester" / "report*.htm*"),
        ]
        for pat in patterns:
            for f in glob.glob(pat):
                p = Path(f)
                try:
                    if p.is_file() and p.stat().st_mtime >= after_ts:
                        found.append(p)
                except:
                    pass

    # newest first
    found.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    return found


def _find_report_in_run_dir(run_dir: Path) -> Path | None:
    """
    Look for report.htm/html in run_dir.
    """
    for name in ("report.html", "report.htm", "report.htm.html", "report"):
        p = run_dir / name
        if p.exists() and p.is_file():
            return p

    # Also accept report*.htm*
    cands = list(run_dir.glob("report*.htm*"))
    if cands:
        cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return cands[0]

    return None


def wait_for_report(run_dir: Path, after_ts: float, max_wait_sec: int = 60) -> Path | None:
    """
    Wait for report to appear. First run_dir, then terminal folders.
    """
    end = _now_ts() + max_wait_sec
    last_terminal_candidates: list[Path] = []

    while _now_ts() <= end:
        p = _find_report_in_run_dir(run_dir)
        if p:
            return p

        last_terminal_candidates = _find_reports_under_terminal(after_ts)
        if last_terminal_candidates:
            return last_terminal_candidates[0]

        time.sleep(1)

    return None


def sanity_params_from_final() -> dict:
    """
    نفس params التي خرجت من final_config.json عندك.
    """
    return {
        "TradeMode": 2,

        "UseMA": True,
        "InpMAfast": 29,
        "InpMAslow": 89,
        "InpMA_Method": 1,
        "InpMA_Price": 1,

        "UseRSI": True,
        "InpRSI_Period": 11,
        "InpRSI_BuyMax": 61,
        "InpRSI_SellMin": 54,

        "InpATR_Period": 23,
        "InpATR_SL_Mult": 1.6,
        "InpRR": 3.0,
        "InpMaxSpreadPts": 350,

        "InpRiskPct": 0.55,
        "MaxTradesPerDay": 9,
        "UseDailyLossStop": True,
        "DailyLossPct": 3.5,

        "UseTrailingStop": True,
        "TS_StartPts": 525,
        "TS_StepPts": 50,

        "UseBreakEven": True,
        "BE_TriggerPts": 170,
        "BE_OffsetPts": 0,

        "MinTradeGapSec": 330,

        # Tech only now
        "UseAISignals": False,
        "BT_EnableAIReplay": False,

        # Disable extras for sanity run
        "UseFibonacciFilter": False,
        "UseRegimeDetector": False,
        "UseRiskGovernor": False,
        "UseMicrostructureFilters": False,
        "UseShadowSL": False,
        "UseLadderTP": False,
        "UsePyramiding": False,
        "Cloud_Enable": False,
    }


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = OUT_ROOT / "_runs" / "sanity_tick"
    _ensure_dir(run_dir)

    # absolute paths
    run_set_abs = str((Path.cwd() / RUN_SET_REL).resolve())
    base_ini_abs = str((Path.cwd() / BASE_INI).resolve())

    params = sanity_params_from_final()
    write_set_utf16le(params, run_set_abs)

    ini_path = run_dir / "run.ini"
    report_prefix = str((run_dir / "report").resolve())  # MT5 will append .htm/.html

    patch_ini(
        base_ini_abs,
        str(ini_path.resolve()),
        expert_name=EXPERT_NAME,
        symbol=SYMBOL,
        period=PERIOD,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        report_prefix_abs=report_prefix,
        expert_params_abs=run_set_abs,
    )

    log_path = run_dir / "mt5_run.log"
    after_ts = _now_ts()

    rc, elapsed = run_mt5_terminal(MT5_TERMINAL, str(ini_path.resolve()), str(log_path.resolve()))
    # Even if rc=0, MT5 may have failed to run tester. We must locate report.

    rep = wait_for_report(run_dir, after_ts=after_ts, max_wait_sec=60)
    if not rep:
        raise FileNotFoundError(
            "Report not found.\n"
            f"- run_dir: {run_dir}\n"
            f"- Check log: {log_path}\n"
            "Tip: افتح MT5 يدويًا ثم Strategy Tester > Journal وشاهد الخطأ الحقيقي "
            "(EA not found / no ticks / login / symbol mismatch).\n"
            f"(rc={rc}, elapsed={elapsed:.1f}s)"
        )

    metrics = parse_mt5_report(str(rep))

    out = {
        "params": params,
        "metrics": metrics,
        "run_dir": str(run_dir.resolve()),
        "rc": rc,
        "elapsed_sec": elapsed,
        "report_found": str(rep.resolve()),
        "mt5_terminal": MT5_TERMINAL,
        "ini": str(ini_path.resolve()),
        "set": run_set_abs,
    }

    (OUT_ROOT / "sanity_result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Saved:", (OUT_ROOT / "sanity_result.json").resolve())
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()