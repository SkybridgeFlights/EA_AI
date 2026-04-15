# tools/update_news_weights.py
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

from app.analytics.news_performance import write_news_weights


def main() -> int:
    # تحميل .env
    env_path = find_dotenv(filename=".env")
    if env_path:
        load_dotenv(env_path, override=True, encoding="utf-8")

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)

    # مسار الناتج يمكن التحكم فيه من ENV، وإلا الافتراضي runtime/news_weights.json
    out_path_str = os.getenv(
        "NEWS_WEIGHTS_PATH",
        str(root / "runtime" / "news_weights.json"),
    )
    out_path = Path(out_path_str)

    # إعدادات افتراضية يمكن تعديلها من ENV
    lookback_days = int(os.getenv("NEWS_LOOKBACK_DAYS", "60"))
    min_trades = int(os.getenv("NEWS_MIN_TRADES_PER_BUCKET", "20"))

    print(f"[news_weights] root     = {root}")
    print(f"[news_weights] JSONL    = {os.getenv('JSONL_PATHS', '')}")
    print(f"[news_weights] out_path = {out_path}")
    print(f"[news_weights] lookback_days={lookback_days}, min_trades={min_trades}")

    try:
        payload = write_news_weights(
            out_path=out_path,
            lookback_days=lookback_days,
            min_trades_per_bucket=min_trades,
        )
    except Exception as e:
        print("[news_weights][ERROR]", e, file=sys.stderr)
        return 1

    weights = payload.get("weights", {})
    print("[news_weights] done. buckets/weights:")
    for k, v in sorted(weights.items()):
        print(f"  - bucket={k!r} -> weight={v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
