# src/diagnose_and_run_mt5.py
from __future__ import annotations

import os
import time
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

from .report_parser import parse_mt5_report

# -------------------- USER SETTINGS --------------------
MT5_TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # ...\tradealpha_research
OUT_ROOT = PROJECT_ROOT / "out_v9_tick" / "_runs" / "sanity_tick"

EXPERT_FILE_NAME = "EA V8.ex5"  # اسم ملف الإكسبرت كما عندك
SYMBOL = "XAUUSDr"
TIMEFRAME = "H1"
FROM_DATE = "2021.01.01"
TO_DATE = "2025.12.31"
DEPOSIT = "10000"

# report prefix (MT5 will append .htm/.html)
REPORT_PREFIX = str((OUT_ROOT / "report").resolve())

# how long to wait for report creation
WAIT_SEC = 90
POLL_EVERY = 1.0
# ------------------------------------------------------


def now_ts() -> float:
    return time.time()


def kill_mt5() -> dict:
    """
    Kill ALL running terminal64.exe processes.
    Returns dict with info.
    """
    # taskkill returns 0 even if nothing was killed sometimes; we capture output
    cmd = ["taskkill", "/F", "/IM", "terminal64.exe", "/T"]
    p = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    return {
        "cmd": " ".join(cmd),
        "rc": p.returncode,
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
    }


def mt5_running() -> bool:
    p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"], capture_output=True, text=True)
    out = (p.stdout or "").lower()
    return "terminal64.exe" in out


def list_terminal_hash_dirs() -> List[Path]:
    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir()]


def find_expert_in_terminal_data(expert_file: str) -> List[Tuple[Path, Path]]:
    """
    Search for expert_file under:
      %APPDATA%\MetaQuotes\Terminal\<HASH>\MQL5\Experts\**
    Returns list of (hash_dir, full_path_to_ex5)
    """
    found = []
    for hdir in list_terminal_hash_dirs():
        experts_root = hdir / "MQL5" / "Experts"
        if not experts_root.exists():
            continue
        hits = list(experts_root.rglob(expert_file))
        for hp in hits:
            if hp.is_file():
                found.append((hdir, hp))
    return found


def choose_best_terminal(found: List[Tuple[Path, Path]]) -> Optional[Tuple[Path, Path]]:
    """
    Choose best match:
    - Most recently modified ex5 wins
    """
    if not found:
        return None
    found.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
    return found[0]


def relative_expert_path(experts_root: Path, expert_full: Path) -> str:
    rel = expert_full.relative_to(experts_root)
    # MT5 expects backslashes
    return str(rel).replace("/", "\\")


def write_ini(ini_path: Path, expert_rel: str) -> None:
    """
    Create run.ini for Strategy Tester.
    IMPORTANT: Expert is relative to MQL5\\Experts
    """
    ini_text = f"""[Tester]
Expert={expert_rel}
Symbol={SYMBOL}
Period={TIMEFRAME}
Optimization=0
Model=0
FromDate={FROM_DATE}
ToDate={TO_DATE}
ForwardMode=0
Deposit={DEPOSIT}
Currency=USD
ProfitInPips=0
Leverage=100
ExecutionMode=0
OptimizationCriterion=1
Visual=0
Report={REPORT_PREFIX}
ReplaceReport=1
ShutdownTerminal=1
"""
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    ini_path.write_text(ini_text, encoding="utf-8")


def run_mt5_config(mt5_terminal: str, ini_path: Path, log_path: Path, timeout_sec: int = 60 * 10) -> dict:
    cmd = [mt5_terminal, f"/config:{str(ini_path.resolve())}"]
    t0 = now_ts()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timeout = False
    try:
        out, err = p.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timeout = True
        p.kill()
        out, err = p.communicate()

    meta = {
        "cmd": cmd,
        "rc": p.returncode,
        "timeout": timeout,
        "elapsed": now_ts() - t0,
        "stdout_len": len(out or ""),
        "stderr_len": len(err or ""),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "CMD:\n" + " ".join(cmd) + "\n\nSTDOUT:\n" + (out or "") + "\n\nSTDERR:\n" + (err or "") + "\n",
        encoding="utf-8",
    )
    return meta


def find_report_local(run_dir: Path) -> Optional[Path]:
    c1 = run_dir / "report.html"
    c2 = run_dir / "report.htm"
    c3 = run_dir / "report.xml"
    for c in (c1, c2, c3):
        if c.exists() and c.stat().st_size > 100:
            return c
    return None


def find_newest_report_in_terminal(hash_dir: Path, after_ts: float) -> Optional[Path]:
    """
    Look for:
      %APPDATA%\\MetaQuotes\\Terminal\\<HASH>\\Tester\\*.htm*
    Also check common subfolders.
    """
    tester = hash_dir / "Tester"
    if not tester.exists():
        return None

    patterns = ["*.htm", "*.html", "*.xml"]
    candidates: List[Path] = []
    for pat in patterns:
        candidates += list(tester.rglob(pat))

    # filter by timestamp and size
    candidates = [p for p in candidates if p.is_file() and p.stat().st_mtime >= after_ts and p.stat().st_size > 500]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def wait_for_report(run_dir: Path, hash_dir: Path, after_ts: float) -> Tuple[Optional[Path], dict]:
    t0 = now_ts()
    while now_ts() - t0 < WAIT_SEC:
        loc = find_report_local(run_dir)
        if loc:
            return loc, {"where": "run_dir", "path": str(loc)}
        ter = find_newest_report_in_terminal(hash_dir, after_ts)
        if ter:
            return ter, {"where": "terminal_tester", "path": str(ter)}
        time.sleep(POLL_EVERY)
    return None, {"where": None, "path": None}


def main():
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    ini_path = OUT_ROOT / "run.ini"
    log_path = OUT_ROOT / "mt5_run.log"

    # 1) locate expert
    found = find_expert_in_terminal_data(EXPERT_FILE_NAME)
    chosen = choose_best_terminal(found)

    if not chosen:
        out = {
            "error": "EXPERT_NOT_FOUND",
            "expert_file": EXPERT_FILE_NAME,
            "hint": (
                "لم أجد الإكسبرت داخل أي مجلد MT5 Data. افتح MT5 -> File -> Open Data Folder "
                "ثم تأكد أن الملف موجود داخل MQL5\\Experts (واعمل Compile)."
            ),
            "searched_base": str((Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal").resolve()),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        raise SystemExit(2)

    hash_dir, expert_full = chosen
    experts_root = hash_dir / "MQL5" / "Experts"
    expert_rel = relative_expert_path(experts_root, expert_full)

    # 2) write ini with correct expert rel path
    write_ini(ini_path, expert_rel)

    # 3) kill MT5 if running (THIS IS THE KEY FIX)
    before = mt5_running()
    kill_info = None
    if before:
        kill_info = kill_mt5()
        time.sleep(1.0)

    # 4) run MT5 config
    start_ts = now_ts()
    run_meta = run_mt5_config(MT5_TERMINAL, ini_path, log_path, timeout_sec=60 * 10)

    # 5) wait for report either in run_dir or terminal tester folders
    report_path, report_found_meta = wait_for_report(OUT_ROOT, hash_dir, after_ts=start_ts)

    final = {
        "python": os.sys.version,
        "mt5_terminal": MT5_TERMINAL,
        "hash_dir": str(hash_dir),
        "experts_root": str(experts_root),
        "expert_full": str(expert_full),
        "expert_rel_used_in_ini": expert_rel,
        "ini_path": str(ini_path),
        "report_prefix": REPORT_PREFIX,
        "mt5_was_running_before": before,
        "kill_info": kill_info,
        "run_meta": run_meta,
        "report_found": report_found_meta,
    }

    if not report_path:
        final["error"] = "REPORT_NOT_FOUND"
        final["hint"] = (
            "إذا ما زال لا يوجد report بعد قتل MT5، فغالبًا هناك خطأ داخل Strategy Tester (Journal) "
            "مثل: EA غير مسموح / لم يتم تحميله / symbol غير موجود / لا توجد بيانات / login. "
            "افتح MT5 -> Strategy Tester -> Journal وانسخ آخر 50 سطر هنا."
        )
        print(json.dumps(final, indent=2, ensure_ascii=False))
        raise SystemExit(3)

    # 6) if report found in terminal folders, copy it to run dir as report.html/htm
    rp = Path(report_path)
    if rp.parent != OUT_ROOT:
        # normalize name to report.html
        dst = OUT_ROOT / ("report.html" if rp.suffix.lower() == ".html" else "report.htm")
        shutil.copy2(str(rp), str(dst))
        rp = dst
        final["report_copied_to"] = str(rp)

    # 7) parse report
    metrics = parse_mt5_report(str(rp))
    out_json = OUT_ROOT / "sanity_result.json"
    payload = {"meta": final, "metrics": metrics}
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nSaved:", out_json)


if __name__ == "__main__":
    main()