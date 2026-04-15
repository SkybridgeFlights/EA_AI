# selfcal_runner.py - SelfCal Runner
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings          # loads .env before os.getenv calls below
from app.core.policy_engine import selfcal_once

ROOT          = Path(__file__).resolve().parents[1]

LOGS          = Path(settings.TRADE_LOGS_DIR)
DEALS_CSV     = Path(settings.DEALS_CSV_PATH)
DECISIONS_CSV = Path(os.getenv("DECISIONS_CSV_PATH",
                     str(ROOT / "runtime" / "logs" / "decisions.csv")))
JSONL_DIR     = Path(os.getenv("JSONL_DIR",          str(ROOT / "runtime" / "logs")))

# Primary: runtime/live_config.json داخل المشروع
# Mirror:  MT5 Common\Files (يقرأ منه الـ EA مباشرة)
LIVE_CFG   = Path(os.getenv("LIVE_CONFIG_PATH",
                 str(ROOT / "runtime" / "live_config.json")))
MIRROR_CFG = os.getenv("MIRROR_LIVE_CONFIG_TO",
                 os.path.join(os.environ.get("APPDATA", ""),
                              "MetaQuotes", "Terminal", "Common", "Files",
                              "live_config.json"))

SYMBOL       = os.getenv("SYMBOL", "XAUUSD")
TF           = os.getenv("SCOPE_TF", "M15")
SHADOW       = os.getenv("SELFCAL_SHADOW", "0").strip().lower() in ("1", "true", "yes")
INTERVAL_SEC = max(60, int(os.getenv("AUTO_SELFCAL_INTERVAL_SEC", "900")))
LOG_FILE     = ROOT / "selfcal_log.txt"


def _log(msg: str) -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [selfcal] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_once() -> dict:
    mirror = Path(MIRROR_CFG) if MIRROR_CFG and MIRROR_CFG.strip() else None
    result = selfcal_once(
        symbol=SYMBOL,
        tf=TF,
        deals_csv=DEALS_CSV,
        decisions_csv=DECISIONS_CSV if DECISIONS_CSV.exists() else None,
        jsonl_dir=JSONL_DIR if JSONL_DIR.exists() else None,
        out_path=LIVE_CFG,
        mirror_path=mirror,
        shadow=SHADOW,
    )
    return result


def run_loop() -> None:
    _log(f"start  symbol={SYMBOL} tf={TF} interval={INTERVAL_SEC}s shadow={SHADOW}")
    _log(f"live_config={LIVE_CFG}")
    _log(f"mirror={MIRROR_CFG or '(none)'}")
    _log(f"deals={DEALS_CSV}")

    while True:
        try:
            result = run_once()
            policy = result.get("policy") or {}
            params = policy.get("params") or {}
            meta   = result.get("_write_meta") or {}
            _log(
                f"OK  "
                f"ver={result.get('policy_version')}  "
                f"shadow={result.get('shadow')}  "
                f"rr={params.get('rr')}  "
                f"risk={params.get('risk_pct')}  "
                f"ai_conf={params.get('ai_min_confidence')}  "
                f"max_trades={params.get('max_trades_per_day')}  "
                f"stability={params.get('stability_mode')}  "
                f"checksum={str(meta.get('checksum', ''))[:12]}"
            )
        except Exception:
            _log("[ERROR]")
            tb = traceback.format_exc()
            print(tb, flush=True)
            try:
                with LOG_FILE.open("a", encoding="utf-8") as f:
                    f.write(tb + "\n")
            except Exception:
                pass

        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    import argparse as _ap
    _p = _ap.ArgumentParser()
    _p.add_argument("--once", action="store_true",
                    help="نفّذ selfcal مرة واحدة وأخرج (للـ Task Scheduler)")
    _args = _p.parse_args()
    if _args.once:
        try:
            result = run_once()
            policy = result.get("policy") or {}
            params = policy.get("params") or {}
            _log(f"OK  rr={params.get('rr')}  risk={params.get('risk_pct')}  "
                 f"regime={params.get('regime_override')}  "
                 f"shadow={result.get('shadow')}")
        except Exception:
            _log("[ERROR]")
            traceback.print_exc()
    else:
        run_loop()
