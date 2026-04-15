# -*- coding: utf-8 -*-
r"""
Promote live_config.json to LIVE (shadow=False) and copy both live_config.json
and AI signal file to MT5 Common\Files.

Usage (from C:\EA_AI):
  py -3.11 tools\promote_live.py
Optional:
  --signal-src ai_signals\xauusd_signal.ini
  --signal-dst xauusd_signal.ini
  --shadow true|false        (default: false)
"""

import argparse, json, os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # C:\EA_AI
RUNTIME_CFG = ROOT / "runtime" / "live_config.json"

def find_common_files_dir() -> Path:
    # Try Roaming first, then Local; create if missing
    home = Path.home()
    candidates = [
        home / "AppData/Roaming/MetaQuotes/Terminal/Common/Files",
        home / "AppData/Local/MetaQuotes/Terminal/Common/Files",
    ]
    for p in candidates:
        if p.exists():
            return p
    # If none exists yet, make Roaming path
    p = candidates[0]
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_cfg(p: Path) -> dict:
    if not p.exists():
        print(f"[error] live_config not found: {p}", file=sys.stderr)
        sys.exit(1)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_cfg(obj: dict, p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def copy_to(dst_dir: Path, src: Path, dst_name: str):
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / dst_name
    shutil.copy2(src, dst)
    print(f"[copy] {src} -> {dst}")
    return dst

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal-src", default=str(ROOT / "ai_signals" / "xauusd_signal.ini"))
    ap.add_argument("--signal-dst", default="ai_signals/xauusd_signal.ini")
    ap.add_argument("--shadow", default="false",
                    help="set to true to keep shadow; default false promotes to live")
    args = ap.parse_args()

    # 1) promote live_config.json (shadow flag)
    cfg = load_cfg(RUNTIME_CFG)
    shadow = str(args.shadow).strip().lower() in ("1", "true", "yes", "y")
    cfg["shadow"] = shadow
    # keep some mirrors if present
    cfg["ai_min_confidence"] = float(cfg.get("ai_min_confidence", 0.6))
    save_cfg(cfg, RUNTIME_CFG)
    state = "shadow=True (canary)" if shadow else "shadow=False (LIVE)"
    print(f"[live_config] updated runtime copy -> {RUNTIME_CFG} | {state}")

    # 2) copy live_config.json to MT5 Common\Files
    common_files = find_common_files_dir()
    cfg_dst = copy_to(common_files, RUNTIME_CFG, "live_config.json")
    print(f"[commit] live_config promoted at: {cfg_dst}")

    # 3) copy AI signal file to Common\Files
    signal_src = Path(args.signal_src)
    if signal_src.exists():
        # keep same relative folder name under Common\Files
        dst_rel = args.signal_dst.replace("\\", "/")
        dst_parent = common_files / Path(dst_rel).parent
        dst_parent.mkdir(parents=True, exist_ok=True)
        signal_dst = dst_parent / Path(dst_rel).name
        shutil.copy2(signal_src, signal_dst)
        print(f"[signal] copied {signal_src} -> {signal_dst}")
    else:
        print(f"[signal] skip (not found): {signal_src}")

    print("[done] promotion + signal sync completed ✔")

if __name__ == "__main__":
    main()







