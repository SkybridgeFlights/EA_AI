# app/analytics/news_performance.py
from __future__ import annotations

import json
import os
import glob
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class NewsBucketMetrics:
    bucket: str              # e.g. "none", "low", "medium", "high" أو "0","1","2","3"
    trades: int
    wins: int
    losses: int
    pf: float                # Profit Factor
    wr: float                # Win Rate %
    mean_R: float            # متوسط R (risk multiple) إن وجد
    pnl: float               # مجموع PnL (إن وجد)
    max_dd_pct: float        # Max Drawdown % على منحنى R أو PnL داخل الباكت نفسه


# ===================== Helpers =====================

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dequote(s: str) -> str:
    return s.strip().strip('"').strip("'")


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(_dequote(p)))).resolve()


def _jsonl_paths_from_env(env_key: str = "JSONL_PATHS") -> List[str]:
    """
    JSONL_PATHS يمكن أن يكون:
      C:/EA_AI/runtime/logs/*.jsonl,C:/EA_AI/other/*.jsonl
    أو مفصول بـ ';'
    """
    raw = os.getenv(env_key, "") or ""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return parts


def _read_jsonl_trades(limit_per_file: int = 100_000) -> pd.DataFrame:
    """
    يقرأ جميع ملفات JSONL المطابقة للـ patterns في JSONL_PATHS
    ويعيد DataFrame موحّدًا.

    نتوقع وجود أعمدة مثل:
      - time          (datetime)
      - symbol
      - R             (risk multiple)
      - pnl / profit  (اختياري)
      - news_level    (0/1/2/3 أو string)
    لكن الدالة مرنة وتتعايش مع الأعمدة الناقصة.
    """
    patterns = _jsonl_paths_from_env()
    if not patterns:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i >= limit_per_file:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            rows.append(obj)
                        except Exception:
                            continue
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # time -> datetime
    if "time" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)

    # أعمدة رقمية
    for col in ("R", "pnl", "profit"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _max_drawdown_pct(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    series = series.fillna(0.0)
    equity = series.cumsum()
    run_max = equity.cummax()
    dd = (equity - run_max) / run_max.replace(0, pd.NA)
    dd = dd.fillna(0.0)
    return float(dd.min() * 100.0)


# ===================== Core logic =====================

def compute_news_buckets(
    df: pd.DataFrame,
    *,
    lookback_days: int = 60,
    min_trades_per_bucket: int = 20,
) -> Dict[str, NewsBucketMetrics]:
    """
    يحسب أداء كل news bucket خلال آخر lookback_days يوم.

    - الباكت يحددها أحد الأعمدة (حسب الترتيب):
        'news_level', 'news_bucket', 'news', 'news_tag'
      وإذا لم توجد؛ نضع "none".
    - نستخدم عمود R إن وجد، وإلا PnL (pnl/profit) لقياس الأداء.
    """
    if df.empty:
        return {}

    # فلترة على آخر lookback_days
    if "time" in df.columns:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=lookback_days)
        df = df[df["time"] >= cutoff].copy()
        if df.empty:
            return {}

    # اختيار عمود الباكت
    bucket_col = None
    for cand in ("news_level", "news_bucket", "news", "news_tag"):
        if cand in df.columns:
            bucket_col = cand
            break

    if bucket_col is None:
        # لا يوجد أي عمود أخبار -> نضع bucket واحد "none"
        df["_bucket"] = "none"
    else:
        df["_bucket"] = df[bucket_col].fillna("none").astype(str)

    # عمود الأداء (R أولاً، وإلا pnl/profit)
    perf_col = "R" if "R" in df.columns else ("pnl" if "pnl" in df.columns else ("profit" if "profit" in df.columns else None))
    if perf_col is None:
        # بدون أي عمود أداء؛ نضع R=0 للجميع
        df["_perf"] = 0.0
    else:
        df["_perf"] = pd.to_numeric(df[perf_col], errors="coerce").fillna(0.0)

    buckets: Dict[str, NewsBucketMetrics] = {}

    for bucket, g in df.groupby("_bucket"):
        trades = int(len(g))
        if trades == 0:
            continue

        R_series = g["_perf"]
        wins = int((R_series > 0).sum())
        losses = int((R_series < 0).sum())

        gross_win = float(R_series[R_series > 0].sum())
        gross_loss = float(R_series[R_series < 0].sum())

        if gross_loss < 0:
            pf = gross_win / abs(gross_loss) if abs(gross_loss) > 0 else 0.0
        else:
            pf = float("inf") if gross_win > 0 else 0.0

        wr = 100.0 * wins / max(1, trades)
        mean_R = float(R_series.mean())
        pnl = float(R_series.sum())
        max_dd_pct = _max_drawdown_pct(R_series)

        buckets[bucket] = NewsBucketMetrics(
            bucket=str(bucket),
            trades=trades,
            wins=wins,
            losses=losses,
            pf=float(pf),
            wr=float(wr),
            mean_R=float(mean_R),
            pnl=float(pnl),
            max_dd_pct=float(max_dd_pct),
        )

    # يمكن تجاهل الباكتات قليلة التداول هنا أو لاحقاً في حساب الأوزان
    # لكننا نُبقيها في النتيجة ونستخدم min_trades_per_bucket في حساب الـ weights.
    return buckets


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def derive_weights_from_buckets(
    buckets: Dict[str, NewsBucketMetrics],
    *,
    min_trades_per_bucket: int = 20,
) -> Dict[str, float]:
    """
    يحوّل NewsBucketMetrics إلى أوزان بين 0.5 و 1.5 تقريبًا:

    - باكت بدون بيانات كافية -> وزن 1.0 (محايد)
    - أداء ضعيف (mean_R وحش / PF ضعيف) -> وزن أقل من 1
    - أداء جيد -> وزن > 1
    """
    weights: Dict[str, float] = {}

    for name, m in buckets.items():
        if m.trades < min_trades_per_bucket:
            weights[name] = 1.0
            continue

        # نستخدم مزيج بسيط من mean_R و PF
        # edge_R في [-2, +2]
        edge_R = _clamp(m.mean_R, -2.0, 2.0)
        # edge_pf في [-1, +1] بعد تحويل PF إلى log-like scale
        # pf<1 -> سالب، pf>1 -> موجب
        if m.pf <= 0:
            edge_pf = -1.0
        else:
            edge_pf_raw = (m.pf - 1.0) / (m.pf + 1.0)  # بين -1 و +1 تقريباً
            edge_pf = _clamp(edge_pf_raw, -1.0, 1.0)

        # مزيج بسيط
        score = 0.6 * edge_R + 0.4 * edge_pf  # في حدود تقريبية [-2, +2]

        # تحويل الـ score إلى وزن حول 1.0
        #  score=0 -> weight=1.0
        #  score=+2 -> weight≈1.4
        #  score=-2 -> weight≈0.6
        weight = 1.0 + 0.2 * score
        weight = _clamp(weight, 0.5, 1.5)

        weights[name] = float(round(weight, 3))

    return weights


def compute_news_weights(
    *,
    lookback_days: int = 60,
    min_trades_per_bucket: int = 20,
    limit_per_file: int = 100_000,
) -> Dict[str, Any]:
    """
    نقطة الدخول الرئيسية التي سيستخدمها tools.update_news_weights:

    - يقرأ JSONL (حسب JSONL_PATHS).
    - يحسب NewsBucketMetrics لكل news bucket.
    - يستخرج weights بين 0.5 و 1.5 تقريبًا.
    - يعيد dict جاهز للكتابة إلى news_weights.json.
    """
    df = _read_jsonl_trades(limit_per_file=limit_per_file)
    buckets = compute_news_buckets(
        df,
        lookback_days=lookback_days,
        min_trades_per_bucket=min_trades_per_bucket,
    )
    weights = derive_weights_from_buckets(
        buckets,
        min_trades_per_bucket=min_trades_per_bucket,
    )

    payload = {
        "updated_at": _now_utc_iso(),
        "lookback_days": lookback_days,
        "min_trades_per_bucket": min_trades_per_bucket,
        "buckets": {name: asdict(m) for name, m in buckets.items()},
        "weights": weights,
    }
    return payload


def write_news_weights(
    out_path: Path,
    *,
    lookback_days: int = 60,
    min_trades_per_bucket: int = 20,
    limit_per_file: int = 100_000,
) -> Dict[str, Any]:
    """
    Helper تُستخدم من السكربت: تحسب + تكتب ملف JSON.

    out_path عادةً: runtime/news_weights.json
    """
    out_path = Path(out_path)
    payload = compute_news_weights(
        lookback_days=lookback_days,
        min_trades_per_bucket=min_trades_per_bucket,
        limit_per_file=limit_per_file,
    )

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = str(out_path) + ".tmp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.replace(tmp, out_path)

    return payload


# ===================== NEW: Loader helper =====================

def load_news_weights_file(path: Optional[Path] = None) -> Dict[str, float]:
    """
    Helper عام لتحميل أوزان الأخبار من ملف:
      - إذا path مُمرّر -> يُستخدم كما هو
      - وإلا:
          * NEWS_WEIGHTS_PATH من env
          * أو C:\\EA_AI\\runtime\\news_weights.json

    يعيد dict: {bucket -> weight(float)} أو {} إذا تعذّر.
    """
    if path is None:
        raw = os.getenv("NEWS_WEIGHTS_PATH", "") or ""
        if raw.strip():
            path = Path(os.path.expandvars(os.path.expanduser(raw.strip())))
        else:
            path = Path(__file__).resolve().parents[2] / "runtime" / "news_weights.json"

    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        data = json.loads(text)
    except Exception:
        return {}

    weights_raw = data.get("weights", {}) or {}
    out: Dict[str, float] = {}
    if isinstance(weights_raw, dict):
        for k, v in weights_raw.items():
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
    return out
