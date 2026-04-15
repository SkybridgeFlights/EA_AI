# tools/regime_classifier.py
# -*- coding: utf-8 -*-
"""
Regime Classifier for XAUUSD H1
Classes: STRONG_TREND / WEAK_TREND / RANGE / HIGH_VOL

التشغيل (من C:\\EA_AI):
  python -m tools.regime_classifier --train          # تدريب + تقييم
  python -m tools.regime_classifier --eval           # تقييم فقط على 2024-2025
  python -m tools.regime_classifier --classify-last  # صنّف آخر شمعة
  python -m tools.regime_classifier --run            # loop كل ساعة
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None
    print("[regime] WARNING: xgboost not installed.")

from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder

# ─────────────────────── Paths ───────────────────────────────────────

ROOT       = Path(__file__).resolve().parents[1]
DATA_CSV   = Path(os.getenv("REGIME_DATA_CSV",   str(ROOT / "data" / "XAUUSD_H1_utf8.csv")))
MODEL_BIN  = Path(os.getenv("REGIME_MODEL_BIN",  str(ROOT / "models" / "regime_model.bin")))
MODEL_META = Path(os.getenv("REGIME_MODEL_META", str(ROOT / "models" / "regime_model_meta.json")))
STATE_FILE = Path(os.getenv("REGIME_STATE_FILE", str(ROOT / "runtime" / "regime_state.json")))
LOOP_SEC   = int(os.getenv("REGIME_LOOP_SEC", "3600"))

TRAIN_START = "2021-01-01"
TRAIN_END   = "2023-12-31"
TEST_START  = "2024-01-01"
TEST_END    = "2026-12-31"

CLASSES = ["HIGH_VOL", "RANGE", "STRONG_TREND", "WEAK_TREND"]  # sorted alpha


# ─────────────────────── Data Loading ────────────────────────────────

def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    time_col = next((c for c in df.columns if c in ("time", "dt", "date")), df.columns[0])
    df["time"] = pd.to_datetime(df[time_col], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)

    rename = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open":   rename[c] = "open"
        elif cl == "high":  rename[c] = "high"
        elif cl == "low":   rename[c] = "low"
        elif cl == "close": rename[c] = "close"
        elif cl in ("volume", "tick_volume", "tickvolume"): rename[c] = "volume"
    df = df.rename(columns=rename)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    if "volume" not in df.columns:
        df["volume"] = 0.0

    print(f"[regime] loaded {len(df)} bars  "
          f"{df['time'].min().date()} to {df['time'].max().date()}")
    return df


# ─────────────────────── Technical Indicators ────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, min_periods=span).mean()


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    return _ema(_true_range(high, low, close), period)


def _adx_full(high: pd.Series, low: pd.Series, close: pd.Series,
              period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (adx, di_pos, di_neg)."""
    tr   = _true_range(high, low, close)
    dm_p = high.diff()
    dm_n = -low.diff()
    dm_p = dm_p.where((dm_p > dm_n) & (dm_p > 0), 0.0)
    dm_n = dm_n.where((dm_n > dm_p) & (dm_n > 0), 0.0)

    atr_s = _ema(tr,   period)
    dmp_s = _ema(dm_p, period)
    dmn_s = _ema(dm_n, period)

    di_p  = 100.0 * dmp_s / atr_s.replace(0, np.nan)
    di_n  = 100.0 * dmn_s / atr_s.replace(0, np.nan)
    dx    = 100.0 * (di_p - di_n).abs() / (di_p + di_n).replace(0, np.nan)
    adx   = _ema(dx.fillna(0), period)
    return adx.fillna(0), di_p.fillna(0), di_n.fillna(0)


def _bb(close: pd.Series, period: int = 20, std: float = 2.0
        ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower)."""
    mid   = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    return mid + std * sigma, mid, mid - std * sigma


# ─────────────────────── Feature Engineering ─────────────────────────
#
# Regla: features must NOT replicate the labeling criterion directly.
# Labels use: vol_ratio (atr_14/atr_50) + adx + di_diff
# Features use: absolute + relative metrics from different perspectives
#

FEATURE_COLS: List[str] = [
    # Volatility (absolute + relative)
    "atr_norm",          # atr_14 / close
    "vol_ratio",         # atr_14 / atr_50  (relative vol)
    "hl_ratio",          # (high-low)/close  per bar
    "bb_width",          # (upper-lower)/mid  (squeeze indicator)
    # Trend strength
    "adx_14",
    "di_diff",           # di_pos - di_neg  (directional bias)
    "di_sum",            # di_pos + di_neg  (total directional activity)
    # Momentum / direction
    "ema_ratio_s",       # ema5/ema20 - 1  (short divergence)
    "ema_ratio_l",       # ema10/ema50 - 1  (long divergence)
    "roc_10",            # rate of change 10 bars
    "roc_24",            # rate of change 24 bars (~1 day)
    "bb_pct_b",          # position inside Bollinger bands (0-1)
    # Candle structure
    "body_ratio",        # |close-open|/(high-low)  body size
    "upper_wick",        # upper wick fraction
    "lower_wick",        # lower wick fraction
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c, o = df["high"], df["low"], df["close"], df["open"]

    feat = pd.DataFrame(index=df.index)
    feat["time"] = df["time"]

    # Volatility
    atr14 = _atr(h, l, c, 14)
    atr50 = _atr(h, l, c, 50)
    feat["atr_norm"]   = (atr14 / c.replace(0, np.nan)).fillna(0)
    feat["vol_ratio"]  = (atr14 / atr50.replace(0, np.nan)).fillna(1.0)
    feat["hl_ratio"]   = ((h - l) / c.replace(0, np.nan)).fillna(0)

    bb_upper, bb_mid, bb_lower = _bb(c, 20)
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    feat["bb_width"]   = (bb_range / bb_mid.replace(0, np.nan)).fillna(0)
    feat["bb_pct_b"]   = ((c - bb_lower) / bb_range).clip(0, 1).fillna(0.5)

    # Trend
    adx14, di_p, di_n = _adx_full(h, l, c, 14)
    feat["adx_14"]  = adx14
    feat["di_diff"] = (di_p - di_n).fillna(0)
    feat["di_sum"]  = (di_p + di_n).fillna(0)

    # Momentum
    ema5  = _ema(c, 5)
    ema10 = _ema(c, 10)
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    feat["ema_ratio_s"] = (ema5  / ema20.replace(0, np.nan) - 1).fillna(0)
    feat["ema_ratio_l"] = (ema10 / ema50.replace(0, np.nan) - 1).fillna(0)
    feat["roc_10"]      = c.pct_change(10).fillna(0)
    feat["roc_24"]      = c.pct_change(24).fillna(0)

    # Candle structure
    bar_range = (h - l).replace(0, np.nan)
    feat["body_ratio"]  = ((c - o).abs() / bar_range).clip(0, 1).fillna(0)
    feat["upper_wick"]  = ((h - pd.concat([c, o], axis=1).max(axis=1)) / bar_range).clip(0, 1).fillna(0)
    feat["lower_wick"]  = ((pd.concat([c, o], axis=1).min(axis=1) - l) / bar_range).clip(0, 1).fillna(0)

    feat = feat.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return feat


# ─────────────────────── Rule-Based Labeling ─────────────────────────
#
# Labeling criteria use different signals from the model features:
#   - HIGH_VOL   : vol_ratio (atr_14/atr_50) is high — current vol vs medium-term vol
#   - STRONG_TREND: adx high AND di spread large (strong directional move)
#   - WEAK_TREND  : moderate adx
#   - RANGE       : low adx + low vol_ratio
#

def label_regimes(feat: pd.DataFrame,
                  thresholds: Optional[Dict] = None) -> pd.Series:
    """Rule-based labeling. Priority: HIGH_VOL > STRONG_TREND > WEAK_TREND > RANGE."""
    if thresholds is None:
        # مضبوطة على البيانات: كل فئة ~25%
        thresholds = {
            "vol_ratio_hv":   1.08,   # p75 of vol_ratio -> HIGH_VOL (~25%)
            "adx_strong":    35.0,   # p60+ ADX -> STRONG_TREND
            "di_diff_strong": 15.0,  # p60 of |di_diff|
            "adx_weak":      26.5,   # p33 ADX -> WEAK_TREND
        }

    vol_ratio  = feat["vol_ratio"]
    adx        = feat["adx_14"]
    di_diff_abs = feat["di_diff"].abs()

    labels = pd.Series("RANGE", index=feat.index, dtype=object)

    # WEAK_TREND: moderate ADX (some directional movement)
    mask_wt = adx >= thresholds["adx_weak"]
    labels[mask_wt] = "WEAK_TREND"

    # STRONG_TREND: high ADX + strong DI separation
    mask_st = (adx >= thresholds["adx_strong"]) & (di_diff_abs >= thresholds["di_diff_strong"])
    labels[mask_st] = "STRONG_TREND"

    # HIGH_VOL: current ATR significantly above medium-term average
    mask_hv = vol_ratio >= thresholds["vol_ratio_hv"]
    labels[mask_hv] = "HIGH_VOL"

    return labels


# ─────────────────────── Train ───────────────────────────────────────

def train(df: pd.DataFrame) -> Tuple:
    if XGBClassifier is None:
        raise RuntimeError("xgboost not installed.")

    feat = build_features(df)

    mask_tr  = (feat["time"] >= TRAIN_START) & (feat["time"] <= TRAIN_END)
    feat_tr  = feat[mask_tr].copy()

    if len(feat_tr) < 500:
        raise ValueError(f"Too few training bars: {len(feat_tr)}")

    print(f"[regime] train bars: {len(feat_tr)}")

    labels_tr = label_regimes(feat_tr)
    dist = labels_tr.value_counts().to_dict()
    print(f"[regime] label distribution (train): {dist}")

    le   = LabelEncoder()
    X_tr = feat_tr[FEATURE_COLS].values.astype("float32")
    y_tr = le.fit_transform(labels_tr)

    # balanced weights (normalized to mean=1)
    counts        = np.bincount(y_tr).astype(float)
    counts        = np.maximum(counts, 1)
    w_per_class   = 1.0 / counts
    sample_weight = w_per_class[y_tr]
    sample_weight = sample_weight / sample_weight.mean()   # normalize

    model = XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.80,
        colsample_bytree=0.80,
        min_child_weight=5,
        objective="multi:softprob",
        num_class=len(le.classes_),
        eval_metric="mlogloss",
        random_state=42,
        tree_method="hist",
        n_jobs=-1,
        reg_lambda=1.5,
        reg_alpha=0.1,
    )

    print("[regime] training ...")
    model.fit(X_tr, y_tr, sample_weight=sample_weight, verbose=False)

    y_pred_tr = model.predict(X_tr)
    acc_tr    = float((y_pred_tr == y_tr).mean())
    print(f"[regime] train accuracy: {acc_tr:.4f}")

    # feature importance
    fi = dict(zip(FEATURE_COLS, model.feature_importances_))
    top5 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]
    print(f"[regime] top features: {top5}")

    thresholds = {
        "vol_ratio_hv":   1.08,
        "adx_strong":    35.0,
        "di_diff_strong": 15.0,
        "adx_weak":       26.5,
    }

    metrics = {"train_acc": round(acc_tr, 4), "train_rows": len(feat_tr)}
    return model, le, FEATURE_COLS, thresholds, metrics


# ─────────────────────── Evaluate ────────────────────────────────────

def evaluate(model, le: LabelEncoder, df: pd.DataFrame) -> Dict:
    feat     = build_features(df)
    mask_te  = (feat["time"] >= TEST_START) & (feat["time"] <= TEST_END)
    feat_te  = feat[mask_te].copy()

    print(f"[regime] test bars: {len(feat_te)}")

    labels_te = label_regimes(feat_te)
    X_te      = feat_te[FEATURE_COLS].values.astype("float32")
    y_te      = le.transform(labels_te)
    y_pred    = model.predict(X_te)
    acc       = float((y_pred == y_te).mean())

    print(f"\n[regime] Test accuracy (2024-2025): {acc:.4f}")
    print(classification_report(y_te, y_pred, target_names=le.classes_, zero_division=0))

    cm = confusion_matrix(y_te, y_pred)
    print("Confusion matrix:")
    print(pd.DataFrame(cm, index=le.classes_, columns=le.classes_).to_string())

    pred_labels = le.inverse_transform(y_pred)
    dist_pred   = pd.Series(pred_labels).value_counts().to_dict()
    true_labels = le.inverse_transform(y_te)
    dist_true   = pd.Series(true_labels).value_counts().to_dict()
    print(f"\nTrue distribution:      {dist_true}")
    print(f"Predicted distribution: {dist_pred}")

    return {"test_acc": round(acc, 4), "test_rows": len(feat_te),
            "dist_true": dist_true, "dist_pred": dist_pred}


# ─────────────────────── Save / Load ─────────────────────────────────

def save_model(model, le: LabelEncoder, feature_cols: List[str],
               thresholds: Dict, metrics: Dict) -> None:
    MODEL_BIN.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_BIN))

    meta = {
        "trained_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        "symbol": "XAUUSD", "tf": "H1",
        "train_period": f"{TRAIN_START} to {TRAIN_END}",
        "test_period":  f"{TEST_START} to {TEST_END}",
        "classes":      list(le.classes_),
        "feature_cols": feature_cols,
        "thresholds":   thresholds,
        "metrics":      metrics,
        "model_bin":    str(MODEL_BIN),
    }
    MODEL_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[regime] model saved: {MODEL_BIN}")
    print(f"[regime] meta  saved: {MODEL_META}")


def load_model():
    """Returns (model, le, feature_cols) or (None, None, None)."""
    if not MODEL_BIN.exists() or not MODEL_META.exists():
        return None, None, None
    if XGBClassifier is None:
        return None, None, None

    meta  = json.loads(MODEL_META.read_text(encoding="utf-8"))
    model = XGBClassifier()
    model.load_model(str(MODEL_BIN))
    le          = LabelEncoder()
    le.classes_ = np.array(meta["classes"])
    return model, le, meta["feature_cols"]


# ─────────────────────── Classify ────────────────────────────────────

def classify_bar(feat_row: pd.Series, model, le: LabelEncoder,
                 feature_cols: List[str]) -> Dict:
    X      = feat_row[feature_cols].values.astype("float32").reshape(1, -1)
    probs  = model.predict_proba(X)[0]
    idx    = int(np.argmax(probs))
    regime = le.classes_[idx]
    conf   = float(probs[idx])
    return {
        "regime":        regime,
        "confidence":    round(conf, 4),
        "probabilities": {str(c): round(float(p), 4)
                          for c, p in zip(le.classes_, probs)},
    }


def write_state(result: Dict, bar_time: datetime,
                feat_row: pd.Series, feature_cols: List[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    key_feats = {k: round(float(feat_row.get(k, 0.0)), 4)
                 for k in ("atr_norm", "vol_ratio", "adx_14",
                            "di_diff", "ema_ratio_l", "bb_width")}
    payload = {
        "symbol":        "XAUUSD",
        "tf":            "H1",
        "updated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bar_time":      bar_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regime":        result["regime"],
        "confidence":    result["confidence"],
        "probabilities": result["probabilities"],
        "features":      key_feats,
    }
    STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[regime] regime={result['regime']} conf={result['confidence']:.3f} "
          f"written to {STATE_FILE.name}")


# ─────────────────────── classify-last / run-loop ────────────────────

def classify_latest() -> Optional[Dict]:
    model, le, feature_cols = load_model()
    if model is None:
        print("[regime] no model found. run --train first.")
        return None

    df       = load_csv(DATA_CSV)
    feat     = build_features(df)
    last_idx = len(feat) - 2          # شمعة مكتملة (قبل الأخيرة)
    feat_row = feat.iloc[last_idx]
    bar_time = pd.to_datetime(feat_row["time"])

    result = classify_bar(feat_row, model, le, feature_cols)
    write_state(result, bar_time, feat_row, feature_cols)

    print(f"[regime] bar={bar_time}  regime={result['regime']}  "
          f"conf={result['confidence']:.3f}")
    print(f"         probs: {result['probabilities']}")
    return result


def run_loop() -> None:
    print(f"[regime] loop start  interval={LOOP_SEC}s  state={STATE_FILE}")
    model, le, feature_cols = load_model()
    if model is None:
        print("[regime] no model found. run --train first.")
        return

    while True:
        try:
            df       = load_csv(DATA_CSV)
            feat     = build_features(df)
            last_idx = len(feat) - 2
            feat_row = feat.iloc[last_idx]
            bar_time = pd.to_datetime(feat_row["time"])
            result   = classify_bar(feat_row, model, le, feature_cols)
            write_state(result, bar_time, feat_row, feature_cols)
        except Exception:
            print("[regime][ERROR]")
            traceback.print_exc()
        time.sleep(LOOP_SEC)


# ─────────────────────── Main ────────────────────────────────────────

def main() -> int:
    global DATA_CSV  # noqa: PLW0603
    ap = argparse.ArgumentParser(description="Regime Classifier XAUUSD H1")
    ap.add_argument("--train",         action="store_true")
    ap.add_argument("--eval",          action="store_true")
    ap.add_argument("--classify-last", action="store_true")
    ap.add_argument("--run",           action="store_true")
    ap.add_argument("--data", type=str, default=str(DATA_CSV))
    args = ap.parse_args()

    DATA_CSV = Path(args.data)

    if not any([args.train, args.eval, args.classify_last, args.run]):
        ap.print_help()
        return 0

    if args.train:
        df = load_csv(DATA_CSV)
        model, le, feature_cols, thresholds, metrics_tr = train(df)
        metrics_te = evaluate(model, le, df)
        metrics_tr.update(metrics_te)
        save_model(model, le, feature_cols, thresholds, metrics_tr)
        print("\n[regime] DONE.")

    elif args.eval:
        df = load_csv(DATA_CSV)
        model, le, feature_cols = load_model()
        if model is None:
            print("[regime] no model found. run --train first.")
            return 1
        evaluate(model, le, df)

    elif args.classify_last:
        if classify_latest() is None:
            return 1

    elif args.run:
        run_loop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
