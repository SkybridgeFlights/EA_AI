# tools/run_pipeline.py
# -*- coding: utf-8 -*-
"""
Full EA_AI Pipeline Runner — يشغّل كل شيء بالترتيب:

  1. sync_jsonl       — مزامنة trades_*.jsonl من MT5 → runtime/logs
  2. build_features   — بناء Feature Store من JSONL
  3. train            — تدريب نموذج XGBoost (اختياري, --skip-train)
  4. news_weights     — تحديث أوزان الأخبار
  5. selfcal          — إعادة معايرة live_config.json
  6. promote          — ترقية إلى MT5 Common\\Files (اختياري, --promote)

التشغيل (من C:\\EA_AI):
  python -m tools.run_pipeline
  python -m tools.run_pipeline --skip-train
  python -m tools.run_pipeline --skip-train --promote
  python -m tools.run_pipeline --shadow --promote
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Tuple

ROOT     = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "pipeline_log.txt"


# ─────────────────────────── logging ────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str, *, also_file: bool = True) -> None:
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    if also_file:
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ─────────────────────────── step runner ────────────────────────────

def _run_step(name: str, fn: Callable) -> bool:
    """يشغّل خطوة واحدة ويعيد True عند النجاح."""
    _log(f"{'─'*10} STEP: {name} {'─'*10}")
    t0 = time.monotonic()
    try:
        rc = fn()
        elapsed = time.monotonic() - t0
        # دعم return codes رقمية (0=OK) ودوال تعيد None
        if rc is None or rc == 0:
            _log(f"[OK] {name} ({elapsed:.1f}s)")
            return True
        else:
            _log(f"[FAIL] {name} returned rc={rc} ({elapsed:.1f}s)")
            return False
    except SystemExit as e:
        elapsed = time.monotonic() - t0
        code = e.code if e.code is not None else 1
        if code == 0:
            _log(f"[OK] {name} ({elapsed:.1f}s)")
            return True
        _log(f"[FAIL] {name} SystemExit(code={code}) ({elapsed:.1f}s)")
        return False
    except Exception:
        elapsed = time.monotonic() - t0
        tb = traceback.format_exc()
        _log(f"[ERROR] {name} ({elapsed:.1f}s)\n{tb}")
        return False


# ─────────────────────────── steps ──────────────────────────────────

def step_sync_jsonl() -> None:
    from tools.sync_jsonl_from_mt5 import sync_jsonl
    sync_jsonl()


def step_build_features() -> int:
    from tools.build_feature_store import main as bfs_main
    return bfs_main([])


def step_train() -> int:
    from tools.train_model_xgb import main as train_main
    return train_main()


def step_news_weights() -> int:
    from tools.update_news_weights import main as nw_main
    return nw_main()


def step_selfcal(shadow: bool) -> None:
    # استدعاء مباشر بدون loop
    import os
    if shadow:
        os.environ["SELFCAL_SHADOW"] = "1"
    from tools.selfcal_runner import run_once
    result = run_once()
    policy = result.get("policy") or {}
    params = policy.get("params") or {}
    meta   = result.get("_write_meta") or {}
    _log(
        f"selfcal result: ver={result.get('policy_version')}  "
        f"shadow={result.get('shadow')}  "
        f"rr={params.get('rr')}  risk={params.get('risk_pct')}  "
        f"ai_conf={params.get('ai_min_confidence')}  "
        f"max_trades={params.get('max_trades_per_day')}  "
        f"stability={params.get('stability_mode')}  "
        f"ok={meta.get('ok')}"
    )


def step_promote(shadow: bool) -> None:
    import sys as _sys
    # نحاكي argparse بحقن sys.argv
    _argv_bak = _sys.argv[:]
    _sys.argv = ["promote_live.py", f"--shadow={'true' if shadow else 'false'}"]
    try:
        from tools.promote_live import main as promote_main
        promote_main()
    finally:
        _sys.argv = _argv_bak


# ─────────────────────────── main ───────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="EA_AI full pipeline runner"
    )
    ap.add_argument(
        "--skip-train", action="store_true",
        help="تخطي خطوة تدريب النموذج"
    )
    ap.add_argument(
        "--promote", action="store_true",
        help="ترقية live_config إلى MT5 بعد selfcal"
    )
    ap.add_argument(
        "--shadow", action="store_true",
        help="تشغيل selfcal وpromote بوضع shadow (بدون كتابة فعلية)"
    )
    ap.add_argument(
        "--skip-news", action="store_true",
        help="تخطي تحديث أوزان الأخبار"
    )
    ap.add_argument(
        "--skip-sync", action="store_true",
        help="تخطي مزامنة JSONL من MT5"
    )
    args = ap.parse_args()

    _log("=" * 50)
    _log("EA_AI Pipeline START")
    _log(f"shadow={args.shadow}  skip_train={args.skip_train}  promote={args.promote}")
    _log("=" * 50)

    results: List[Tuple[str, bool]] = []

    # ── 1. Sync JSONL ──
    if not args.skip_sync:
        ok = _run_step("sync_jsonl", step_sync_jsonl)
        results.append(("sync_jsonl", ok))
    else:
        _log("[SKIP] sync_jsonl")

    # ── 2. Build Features ──
    ok = _run_step("build_features", step_build_features)
    results.append(("build_features", ok))
    # لا نوقف الـ pipeline لو feature store فارغ — selfcal يعمل بدونه

    # ── 3. Train ──
    if not args.skip_train:
        ok = _run_step("train_model", step_train)
        results.append(("train_model", ok))
    else:
        _log("[SKIP] train_model")

    # ── 4. News Weights ──
    if not args.skip_news:
        ok = _run_step("news_weights", step_news_weights)
        results.append(("news_weights", ok))
    else:
        _log("[SKIP] news_weights")

    # ── 5. SelfCal ── (حرجة: إذا فشلت نوقف)
    ok = _run_step("selfcal", lambda: step_selfcal(args.shadow))
    results.append(("selfcal", ok))
    if not ok:
        _log("[ABORT] selfcal failed — aborting pipeline")
        _print_summary(results)
        return 1

    # ── 6. Promote ──
    if args.promote:
        ok = _run_step("promote", lambda: step_promote(args.shadow))
        results.append(("promote", ok))
    else:
        _log("[SKIP] promote (pass --promote to enable)")

    _print_summary(results)
    failed = sum(1 for _, ok in results if not ok)
    return 0 if failed == 0 else 1


def _print_summary(results: List[Tuple[str, bool]]) -> None:
    _log("=" * 50)
    _log("PIPELINE SUMMARY")
    for name, ok in results:
        status = "OK  " if ok else "FAIL"
        _log(f"  [{status}] {name}")
    failed = sum(1 for _, ok in results if not ok)
    _log(f"Result: {len(results) - failed}/{len(results)} steps passed")
    _log("=" * 50)


if __name__ == "__main__":
    raise SystemExit(main())
