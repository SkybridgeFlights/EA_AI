# tools/build_feature_store.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from app.config import settings


# ============================= Helpers =============================


def _log(msg: str) -> None:
    print(f"[feature_store] {msg}")


def _clip_series(
    s: pd.Series,
    lo: float,
    hi: float,
) -> pd.Series:
    return s.clip(lower=lo, upper=hi)


def _safe_num(
    df: pd.DataFrame,
    colnames: List[str],
    default: float = np.nan,
) -> pd.Series:
    """
    تحاول اختيار أول عمود موجود من colnames وتحويله إلى أرقام.
    """
    for c in colnames:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def _ensure_datetime(df: pd.DataFrame, col: str = "time") -> pd.Series:
    """
    يحول عمود time إلى datetime[utc] قدر الإمكان.
    """
    if col not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")

    s = df[col]
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.tz_convert("UTC") if s.dt.tz is not None else s.dt.tz_localize("UTC")

    # محاولات متعددة
    out = pd.to_datetime(s, errors="coerce", utc=True)
    return out


def _load_jsonl_files(jsonl_dir: Path, prefix: str) -> List[Dict[str, Any]]:
    """
    يقرأ جميع ملفات JSONL من المجلد المحدد بالبادئة المعطاة (trades_*.jsonl)
    ويعيد قائمة من السجلات (dict) جاهزة للتحويل إلى DataFrame.
    """
    if not jsonl_dir.exists():
        _log(f"JSONL_DIR not found: {jsonl_dir}")
        return []

    pattern = f"{prefix}*.jsonl"
    files = sorted(jsonl_dir.glob(pattern))
    if not files:
        _log(f"no JSONL files matching {pattern} in {jsonl_dir}")
        return []

    all_rows: List[Dict[str, Any]] = []
    for f in files:
        _log(f"reading {f}")
        try:
            with f.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            all_rows.append(row)
                    except json.JSONDecodeError:
                        # نتجاهل أي سطر مكسور بدون إيقاف السكربت
                        continue
        except Exception as e:
            print(f"[feature_store][WARN] cannot read {f}: {e}", file=sys.stderr)
            continue

    _log(f"total records loaded: {len(all_rows)}")
    return all_rows


# ====================== Feature Engineering ========================


def _build_base_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    يبني DataFrame أساسي من JSONL rows مع:
      - time (datetime)
      - R (target)
      - ai_conf
      - spread_open
      - regime
      - news_level
      - direction
    """
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # time
    df["time"] = _ensure_datetime(df, "time")

    # R (target) – نحاول من عدة أعمدة محتملة
    if "R" in df.columns:
        df["R"] = pd.to_numeric(df["R"], errors="coerce")
    else:
        # نحاول من pnl أو profit / risk
        pnl = _safe_num(df, ["pnl", "profit"], default=np.nan)
        risk_r = _safe_num(df, ["risk_r", "risk_multiple"], default=np.nan)
        df["R"] = pnl / risk_r.replace({0.0: np.nan})  # قد ينتج NaN

    # تنظيف R وقصّ القيم المتطرفة
    df["R"] = pd.to_numeric(df["R"], errors="coerce")
    df["R"] = _clip_series(df["R"], lo=-10.0, hi=10.0)

    # ai_conf
    ai_conf = _safe_num(df, ["ai_conf", "ai_conf_bucket", "ai_confidence"], default=np.nan)
    df["ai_conf"] = _clip_series(ai_conf, lo=0.0, hi=1.0)

    # spread_open
    df["spread_open"] = _safe_num(
        df,
        ["spread_open", "spread_pts", "spread"],
        default=np.nan,
    )

    # atr تقريبية (إذا موجودة)
    df["atr"] = _safe_num(
        df,
        ["atr", "atr_pts", "atr_points", "atr_pips"],
        default=np.nan,
    )

    # regime (نحتفظ بالنص + كود رقمي)
    regime_raw = None
    for c in ("regime", "regime_name", "market_regime"):
        if c in df.columns:
            regime_raw = df[c].astype(str).str.lower()
            break
    if regime_raw is None:
        regime_raw = pd.Series(["unknown"] * len(df), index=df.index)
    df["regime"] = regime_raw

    # news_level / news_bucket
    news_col = None
    for c in ("news_level", "news_bucket", "news", "news_tag"):
        if c in df.columns:
            news_col = df[c]
            break
    if news_col is None:
        news_raw = pd.Series(["none"] * len(df), index=df.index)
    else:
        news_raw = news_col.fillna("none").astype(str)
    df["news_bucket"] = news_raw

    # direction (buy/sell) -> numeric
    dir_raw = None
    for c in ("dir", "direction", "side", "position", "order_type"):
        if c in df.columns:
            dir_raw = df[c].astype(str).str.upper()
            break
    if dir_raw is None and "type" in df.columns:
        dir_raw = df["type"].astype(str).str.upper()
    if dir_raw is None:
        dir_raw = pd.Series(["UNKNOWN"] * len(df), index=df.index)

    def _map_dir(x: str) -> int:
        x = (x or "").upper()
        if x in ("BUY", "LONG", "B", "1"):
            return 1
        if x in ("SELL", "SHORT", "S", "-1"):
            return -1
        return 0

    df["direction_code"] = dir_raw.map(_map_dir).astype("int8")

    return df


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    يضيف ميزات زمنية: hour, weekday, month, is_london_session, is_ny_session
    مع معالجة NaT بملء -1 قبل التحويل لـ int.
    """
    time = df["time"]

    # hour / weekday / month مع fillna(-1) لتفادي IntCastingNaNError
    hour = time.dt.hour.astype("float64").fillna(-1.0)
    weekday = time.dt.weekday.astype("float64").fillna(-1.0)
    month = time.dt.month.astype("float64").fillna(-1.0)

    df["hour"] = hour.astype("int16")
    df["weekday"] = weekday.astype("int16")
    df["month"] = month.astype("int16")

    # جلسات تقريبية على XAU / FX
    h = df["hour"]
    df["is_london_session"] = ((h >= 7) & (h <= 16)).astype("int8")
    df["is_ny_session"] = ((h >= 12) & (h <= 21)).astype("int8")

    return df


def _add_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    ميزات Volatility و Spread:
      - spread_open
      - spread_bucket (0=low,1=mid,2=high)
      - atr
      - vol_bucket (0=low,1=mid,2=high)
    """
    # spread
    spread = pd.to_numeric(df["spread_open"], errors="coerce")
    df["spread_open"] = spread

    # spread bucket من quantiles
    try:
        q1, q2 = spread.quantile([0.33, 0.66])
    except Exception:
        q1, q2 = np.nan, np.nan

    def _bucket_spread(x: float) -> int:
        if np.isnan(x) or np.isnan(q1) or np.isnan(q2):
            return 1  # mid
        if x <= q1:
            return 0
        if x >= q2:
            return 2
        return 1

    df["spread_bucket"] = spread.apply(_bucket_spread).astype("int8")

    # ATR
    atr = pd.to_numeric(df["atr"], errors="coerce")
    df["atr"] = atr

    try:
        qa1, qa2 = atr.quantile([0.33, 0.66])
    except Exception:
        qa1, qa2 = np.nan, np.nan

    def _bucket_vol(x: float) -> int:
        if np.isnan(x) or np.isnan(qa1) or np.isnan(qa2):
            return 1
        if x <= qa1:
            return 0
        if x >= qa2:
            return 2
        return 1

    df["vol_bucket"] = atr.apply(_bucket_vol).astype("int8")

    return df


def _add_news_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    تحويل news_bucket إلى:
      - news_bucket_clean (str)
      - news_impact_code (0..3)
    """
    raw = df["news_bucket"].astype(str).str.strip().str.lower()

    def _normalize_news(x: str) -> str:
        if x in ("", "none", "nan", "no", "0"):
            return "none"
        if x in ("low", "1", "l"):
            return "low"
        if x in ("medium", "med", "2", "m"):
            return "medium"
        if x in ("high", "3", "h"):
            return "high"
        return x  # fallback

    nb = raw.map(_normalize_news)
    df["news_bucket_clean"] = nb

    def _map_impact(x: str) -> int:
        if x == "none":
            return 0
        if x == "low":
            return 1
        if x == "medium":
            return 2
        if x == "high":
            return 3
        # أي شيء آخر نعتبره medium
        return 2

    df["news_impact_code"] = nb.map(_map_impact).astype("int8")
    return df


def _add_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    يحوّل regime (string) إلى كود رقمي + one-hot بسيط لأهم الأنماط.
    """
    reg = df["regime"].astype(str).str.lower()

    known = ["trend", "range", "reversal", "breakout"]

    def _map_reg(x: str) -> int:
        if x in known:
            return known.index(x) + 1  # 1..N
        if x in ("unknown", "", "nan", "none"):
            return 0
        return len(known) + 1  # other

    df["regime_code"] = reg.map(_map_reg).astype("int8")
    # أعمدة ثنائية للأنماط الأكثر شيوعًا
    for name in known:
        col = f"regime_is_{name}"
        df[col] = (reg == name).astype("int8")

    return df


def _rolling_stats(series: pd.Series, window: int, min_periods: int) -> Dict[str, pd.Series]:
    """
    يحسب rolling mean/std/maxdd تقريبية لسلسلة R.
    """
    roll = series.rolling(window=window, min_periods=min_periods)

    mean = roll.mean()
    std = roll.std()

    # max drawdown التقريبية: نحسب cumulative R، ثم rolling max، ثم الفرق
    # (نفس النافذة)
    cum = series.cumsum()
    roll_max = cum.rolling(window=window, min_periods=min_periods).max()
    dd = roll_max - cum
    maxdd = dd  # هنا نحفظ آخر dd في كل نافذة، تقريبية كـ feature

    return {
        "mean": mean,
        "std": std,
        "maxdd": maxdd,
    }


def _add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    ميزات Rolling على R:
      - roll_R_mean_20 / 50 / 100
      - roll_R_std_20 / 50 / 100
      - roll_R_maxdd_50 / 100
    """
    # نرتّب حسب الوقت تصاعديًا ثم نرجع الأصلية في النهاية
    df_sorted = df.sort_values("time").reset_index()
    idx = df_sorted["index"]
    R = pd.to_numeric(df_sorted["R"], errors="coerce").fillna(0.0)

    configs = [
        (20, 5),
        (50, 10),
        (100, 20),
    ]

    feat_mean = {}
    feat_std = {}
    feat_dd = {}

    for window, minp in configs:
        stats = _rolling_stats(R, window=window, min_periods=minp)
        feat_mean[window] = stats["mean"]
        feat_std[window] = stats["std"]

    # max drawdown فقط للـ 50 و 100
    for window, minp in [(50, 10), (100, 20)]:
        stats = _rolling_stats(R, window=window, min_periods=minp)
        feat_dd[window] = stats["maxdd"]

    # نعيدها إلى ترتيب df الأصلي
    for window, s in feat_mean.items():
        df.loc[idx, f"roll_R_mean_{window}"] = s.values
    for window, s in feat_std.items():
        df.loc[idx, f"roll_R_std_{window}"] = s.values
    for window, s in feat_dd.items():
        df.loc[idx, f"roll_R_maxdd_{window}"] = s.values

    # تنظيف NaN بتعويضها بـ 0 (محافظ)
    for col in [c for c in df.columns if c.startswith("roll_R_")]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


def engineer_features(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    نقطة الدخول الأساسية لبناء Feature Store من JSONL rows.
    """
    base = _build_base_df(rows)
    if base.empty:
        _log("dataframe is empty after _build_base_df.")
        return base

    # نزيل الصفوف التي لا تحتوي على target صالح
    base["R"] = pd.to_numeric(base["R"], errors="coerce")
    base = base.replace([np.inf, -np.inf], np.nan)
    base = base.dropna(subset=["R"])
    if base.empty:
        _log("no rows with valid R target. nothing to train on.")
        return base

    # Features زمنية
    base = _add_time_features(base)

    # Features Volatility
    base = _add_volatility_features(base)

    # Features أخبار
    base = _add_news_features(base)

    # Features Regime
    base = _add_regime_features(base)

    # Rolling Features
    base = _add_rolling_features(base)

    # بعض الأعمدة الإضافية إن وجدت: slippage, mfe, mae, tick_imbalance ...
    for colname in [
        "slippage_pts",
        "mfe_pts",
        "mae_pts",
        "tick_imbalance",
        "spread_close",
    ]:
        if colname in base.columns:
            base[colname] = pd.to_numeric(base[colname], errors="coerce").fillna(0.0)

    # إزالة أعمدة غير ضرورية/ثقيلة (مثل نصوص طويلة)
    for colname in list(base.columns):
        if colname in ("comment", "why", "ai_reason", "news_title", "news_text"):
            base.drop(columns=[colname], inplace=True, errors="ignore")

    _log(
        "engineered features: rows={rows} cols={cols}".format(
            rows=len(base), cols=len(base.columns)
        )
    )
    return base


# ========================== Main Builder ===========================


def build_feature_store(
    out_dir: Path,
    jsonl_dir: Path,
    prefix: str,
) -> Optional[Path]:
    """
    يبني Feature Store متقدم من trades_*.jsonl:

    - يكتب:
        out_dir / "features.parquet"
        out_dir / "features.csv"
    - بالإضافة إلى مسار settings.FEATURE_STORE_PATH إذا كان مضبوطًا
      (مثلاً: C:\\EA_AI\\runtime\\features\\features.parquet)
    """
    rows = _load_jsonl_files(jsonl_dir=jsonl_dir, prefix=prefix)
    if not rows:
        _log("no rows loaded, nothing to write.")
        return None

    df = engineer_features(rows)
    if df.empty:
        _log("engineered DataFrame is empty. nothing to write.")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = out_dir / "features.parquet"
    csv_path = out_dir / "features.csv"

    _log(f"writing Parquet -> {parquet_path}")
    df.to_parquet(parquet_path, index=False)

    _log(f"writing CSV -> {csv_path}")
    df.to_csv(csv_path, index=False)

    # mirror إلى FEATURE_STORE_PATH إذا محدد في settings
    feat_path_setting = getattr(settings, "FEATURE_STORE_PATH", None)
    if feat_path_setting:
        try:
            dest = Path(feat_path_setting).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            _log(f"mirroring Parquet -> {dest}")
            df.to_parquet(dest, index=False)
        except Exception as e:
            print(f"[feature_store][WARN] mirror to FEATURE_STORE_PATH failed: {e}", file=sys.stderr)

    _log(
        "done. rows={rows} cols={cols} out_dir={out}".format(
            rows=len(df), cols=len(df.columns), out=out_dir
        )
    )
    return parquet_path


# ============================= CLI ================================


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build advanced feature store from trades_*.jsonl"
    )
    parser.add_argument(
        "--jsonl-dir",
        type=str,
        default=str(Path(settings.JSONL_DIR)),
        help="Source JSONL directory (default from settings.JSONL_DIR)",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=settings.JSONL_FILE_PREFIX,
        help="JSONL file prefix (default from settings.JSONL_FILE_PREFIX)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(Path(getattr(settings, "FEATURE_STORE_ROOT", "runtime/features"))),
        help="Output directory for feature store (default from settings.FEATURE_STORE_ROOT)",
    )

    args = parser.parse_args(argv)

    jsonl_dir = Path(args.jsonl_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    _log(f"JSONL_DIR = {jsonl_dir}")
    _log(f"PREFIX    = {args.prefix}")
    _log(f"OUT_DIR   = {out_dir}")

    build_feature_store(
        out_dir=out_dir,
        jsonl_dir=jsonl_dir,
        prefix=args.prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
