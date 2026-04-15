import os
import re
import sys
import shutil
import glob
from datetime import datetime


MT5_PATH_DEFAULT = r"C:\Program Files\MetaTrader 5\terminal64.exe"
INI_DEFAULT = r"C:\tradealpha_research\temp\run.ini"

EXPECTED_SET_NAME = "auto_test.set"

# قيم إجبارية لضمان TECH ONLY وعدم أي AI خلال الباكتيست الآلي
FORCE_TECH_KV = {
    "TradeMode": "2",
    "UseAISignals": "false",
    "BT_EnableAIReplay": "false",
    "UseLiveConfig": "false",
    "Cloud_Enable": "false",
    "Cloud_FetchAI": "false",
    "BT_DisableCloud": "true",
    "BT_DisableCalendar": "true",
}


def hr(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def parse_ini(ini_path: str) -> dict:
    if not os.path.exists(ini_path):
        raise FileNotFoundError(ini_path)

    data = {}
    current = None
    with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            m = re.match(r"^\[(.+)\]$", line)
            if m:
                current = m.group(1)
                data[current] = {}
                continue
            if "=" in line and current:
                k, v = line.split("=", 1)
                data[current][k.strip()] = v.strip()
    return data


def find_terminal_data_dirs() -> list[str]:
    base = os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal")
    if not os.path.isdir(base):
        return []

    dirs = []
    # Hash folders
    for name in os.listdir(base):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def tester_profile_path_from_terminal_dir(term_dir: str) -> str:
    return os.path.join(term_dir, "MQL5", "Profiles", "Tester")


def install_tester_profile_path(mt5_path: str) -> str:
    # C:\Program Files\MetaTrader 5\terminal64.exe -> C:\Program Files\MetaTrader 5\MQL5\Profiles\Tester
    root = os.path.dirname(os.path.abspath(mt5_path))
    return os.path.join(root, "MQL5", "Profiles", "Tester")


def latest_tester_log(term_dir: str) -> str | None:
    logs_dir = os.path.join(term_dir, "Tester", "logs")
    if not os.path.isdir(logs_dir):
        return None
    logs = sorted(glob.glob(os.path.join(logs_dir, "*.log")), key=os.path.getmtime, reverse=True)
    return logs[0] if logs else None


def read_log_tail_any_encoding(log_path: str, max_bytes: int = 20000) -> str:
    if not log_path or not os.path.exists(log_path):
        return ""
    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        blob = f.read()

    # MT5 logs often UTF-16LE
    for enc in ("utf-16le", "utf-8", "cp1252"):
        try:
            return blob.decode(enc, errors="ignore")
        except Exception:
            continue
    return ""


def normalize_set_content(text: str) -> str:
    """
    Ensure key=value lines, replace or append FORCE_TECH_KV keys.
    """
    lines = []
    seen = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in FORCE_TECH_KV:
            lines.append(f"{k}={FORCE_TECH_KV[k]}")
            seen.add(k)
        else:
            lines.append(f"{k}={v}")

    # append missing forced keys
    for k, v in FORCE_TECH_KV.items():
        if k not in seen:
            lines.append(f"{k}={v}")

    return "\n".join(lines) + "\n"


def force_set_file(source_set_path: str) -> str:
    with open(source_set_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    forced = normalize_set_content(content)
    with open(source_set_path, "w", encoding="utf-8") as f:
        f.write(forced)
    return source_set_path


def rewrite_ini_expertparameters_name_only(ini_path: str, set_name: str):
    data = parse_ini(ini_path)
    if "Tester" not in data:
        raise RuntimeError("Missing [Tester] section in INI")

    # Rewrite file preserving minimal structure
    tester = data["Tester"]
    tester["ExpertParameters"] = os.path.basename(set_name)

    lines = []
    lines.append("[Tester]")
    for k, v in tester.items():
        lines.append(f"{k}={v}")
    content = "\n".join(lines) + "\n"

    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(content)


def copy_set_to_targets(source_set: str, mt5_path: str) -> list[str]:
    targets = []

    # 1) all terminal data dirs
    for term_dir in find_terminal_data_dirs():
        tdir = tester_profile_path_from_terminal_dir(term_dir)
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, EXPECTED_SET_NAME)
        shutil.copy2(source_set, dst)
        targets.append(dst)

    # 2) install dir tester profile
    inst = install_tester_profile_path(mt5_path)
    os.makedirs(inst, exist_ok=True)
    dst2 = os.path.join(inst, EXPECTED_SET_NAME)
    shutil.copy2(source_set, dst2)
    targets.append(dst2)

    return targets


def main():
    ini_path = os.environ.get("MT5_INI", INI_DEFAULT)
    mt5_path = os.environ.get("MT5_PATH", MT5_PATH_DEFAULT)

    ini = parse_ini(ini_path)
    tester = ini.get("Tester", {})

    hr("INI PARSED")
    print(f"INI_PATH: {os.path.abspath(ini_path)}")
    print(f"Expert: {tester.get('Expert')}")
    print(f"ExpertParameters (raw): {tester.get('ExpertParameters')}")
    print(f"Report: {tester.get('Report')}")

    hr("TERMINALS FOUND")
    term_dirs = find_terminal_data_dirs()
    if not term_dirs:
        print("No terminal data dirs found.")
    else:
        for td in term_dirs[:30]:
            name = os.path.basename(td)
            set_path = os.path.join(tester_profile_path_from_terminal_dir(td), EXPECTED_SET_NAME)
            print(f"{name} | set exists: {os.path.exists(set_path)} | {set_path}")

    hr("INSTALL DIR CHECK")
    inst_dir = install_tester_profile_path(mt5_path)
    inst_set = os.path.join(inst_dir, EXPECTED_SET_NAME)
    print(f"Install Tester Dir: {inst_dir}")
    print(f"Install set exists: {os.path.exists(inst_set)} | {inst_set}")

    # Best guess active terminal: one with latest tester log
    hr("ACTIVE TERMINAL (BEST GUESS)")
    best = None
    best_mtime = 0
    best_log = None
    for td in term_dirs:
        lp = latest_tester_log(td)
        if lp and os.path.exists(lp):
            m = os.path.getmtime(lp)
            if m > best_mtime:
                best_mtime = m
                best = td
                best_log = lp

    if best:
        print(f"Terminal hash: {os.path.basename(best)}")
        print(f"Active log: {best_log}")
        print(f"Active log mtime: {datetime.fromtimestamp(best_mtime)}")

        hr("LAST 120 LOG LINES (RAW TAIL)")
        tail = read_log_tail_any_encoding(best_log)
        tail_lines = tail.splitlines()[-120:]
        for ln in tail_lines:
            print(ln)
    else:
        print("No tester logs found in any terminal dir.")

    # Auto-fix
    if os.environ.get("MT5_DIAG_FIX", "0") == "1":
        hr("AUTO-FIX APPLIED")
        # Source set could be in temp or anywhere; prefer temp/auto_test.set if exists
        temp_dir = os.path.dirname(os.path.abspath(ini_path))
        source_candidates = [
            os.path.join(temp_dir, EXPECTED_SET_NAME),
            os.path.join(temp_dir, "test.set"),
            os.path.join(temp_dir, "auto_test.set"),
        ]
        source = None
        for c in source_candidates:
            if os.path.exists(c):
                source = c
                break
        if not source:
            raise FileNotFoundError("No source set file found in temp directory.")

        # Force TECH ONLY keys inside source
        force_set_file(source)

        # Copy to all targets including install dir
        targets = copy_set_to_targets(source, mt5_path)
        print(f"Copied set to {len(targets)} targets. Example:\n  {targets[0]}")

        # Force INI ExpertParameters to name-only
        rewrite_ini_expertparameters_name_only(ini_path, EXPECTED_SET_NAME)
        print(f"INI forced: ExpertParameters={EXPECTED_SET_NAME}")

    hr("DONE")
    print("If you want auto-fix + copy to install dir + rewrite INI name-only:")
    print("PowerShell:")
    print("  $env:MT5_DIAG_FIX=1; python -m src.core.mt5_diag")


if __name__ == "__main__":
    main()