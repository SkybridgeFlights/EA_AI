# app/ml/model.py
# XGBoost training + inference (Company-grade, anti-collapse)
# - PRIMARY training source: MT5 exported CSV via env TRAIN_PRICES_CSV
# - Time-based split (last 20% test)
# - Balanced sample_weight normalized to mean=1.0 (stabilizes training)
# - Deterministic training by default (n_jobs=1) to stop "sometimes works/sometimes collapses"
# - Built-in sanity checks to detect uniform predictions early
# - Writes mapping_{SYMBOL}.json : {class_index -> original_label(-1/0/+1)}
# - Writes/updates active_model.json via registry.set_active_model()

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple, Optional, List

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.metrics import classification_report
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

from app.config import settings
from app.ml.features import make_features, make_labels
from app.ml.registry import save_model_binary, load_active_model, set_active_model


# ==========================================================
# Time/index helpers
# ==========================================================
def _ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df[~df.index.to_series().isna()]
    df = df[~df.index.duplicated(keep="last")]
    return df.sort_index()


def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").strip()
    return s if s else getattr(settings, "SYMBOL", "XAUUSDr")


# ==========================================================
# MT5 CSV loader (PRIMARY)
# ==========================================================
def _load_prices_for_training(_: str) -> pd.DataFrame:
    csv_path = os.environ.get("TRAIN_PRICES_CSV", "").strip()
    if not csv_path:
        raise RuntimeError("TRAIN_PRICES_CSV not set")
    if not os.path.exists(csv_path):
        raise RuntimeError(f"CSV not found: {csv_path}")

    # Try multiple encodings — MT5 exports are often UTF-16
    _tried = []
    df = None
    for _enc in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"):
        try:
            df = pd.read_csv(csv_path, sep=None, engine="python", encoding=_enc)
            print(f"[TRAIN] CSV encoding detected: {_enc}")
            break
        except (UnicodeDecodeError, Exception) as _e:
            _tried.append(f"{_enc}:{_e}")
    if df is None:
        raise RuntimeError(f"Cannot decode {csv_path}. Tried: {_tried}")
    df.columns = [str(c).strip() for c in df.columns]
    cols_l = {str(c).lower(): c for c in df.columns}

    # time detection
    if "time" in cols_l:
        df["dt"] = pd.to_datetime(df[cols_l["time"]], utc=True, errors="coerce")
    elif "dt" in cols_l:
        df["dt"] = pd.to_datetime(df[cols_l["dt"]], utc=True, errors="coerce")
    elif "date" in cols_l and "time" in cols_l:
        df["dt"] = pd.to_datetime(
            df[cols_l["date"]].astype(str) + " " + df[cols_l["time"]].astype(str),
            utc=True,
            errors="coerce",
        )
    else:
        df["dt"] = pd.to_datetime(df.iloc[:, 0], utc=True, errors="coerce")

    df = df.dropna(subset=["dt"]).copy()
    df = df.sort_values("dt").drop_duplicates("dt", keep="last")
    df = df.set_index("dt")

    # normalize OHLCV names
    rename_map = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl == "open":
            rename_map[c] = "Open"
        elif cl == "high":
            rename_map[c] = "High"
        elif cl == "low":
            rename_map[c] = "Low"
        elif cl == "close":
            rename_map[c] = "Close"
        elif cl in ("tick_volume", "real_volume", "volume", "tickvolume", "realvolume"):
            rename_map[c] = "Volume"

    df = df.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close"]
    for r in required:
        if r not in df.columns:
            raise RuntimeError(f"Missing column: {r}. Found: {list(df.columns)}")

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).astype(float)
    df = _ensure_utc_index(df)

    print("[TRAIN] using MT5 CSV:", csv_path)
    print("[TRAIN] rows:", len(df))
    return df


# ==========================================================
# yfinance fallback (LIVE inference only)
# ==========================================================
def _best_yf_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    if s == "XAUUSD" and getattr(settings, "USE_GC_F_FOR_XAU", False):
        return "GC=F"
    if s.endswith("USD") and len(s) == 6:
        return s + "=X"
    return s


def _flatten_yf_columns(df: pd.DataFrame, yf_symbol: str) -> pd.DataFrame:
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    try:
        if yf_symbol in df.columns.get_level_values(1):
            return df.xs(yf_symbol, axis=1, level=1)
        if yf_symbol in df.columns.get_level_values(0):
            return df.xs(yf_symbol, axis=1, level=0)
        first_t = df.columns.get_level_values(1)[0]
        return df.xs(first_t, axis=1, level=1)
    except Exception:
        try:
            df.columns = df.columns.get_level_values(-1)
        except Exception:
            pass
        return df


def _fetch_prices(symbol: str, lookback_days: int) -> pd.DataFrame:
    yf_symbol = _best_yf_symbol(symbol)
    interval = "30m" if lookback_days <= 60 else "60m"
    period_days = max(lookback_days, 5) if lookback_days <= 60 else min(lookback_days, 730)

    try:
        df = yf.download(
            yf_symbol,
            period=f"{period_days}d",
            interval=interval,
            auto_adjust=False,
            progress=False,
            group_by="column",
        )
    except Exception:
        return pd.DataFrame()

    df = _ensure_utc_index(df)
    if df.empty:
        return pd.DataFrame()

    df = _flatten_yf_columns(df, yf_symbol)
    if "Close" not in df.columns and "Adj Close" in df.columns:
        df["Close"] = df["Adj Close"]
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    need = ["Open", "High", "Low", "Close"]
    if any(c not in df.columns for c in need):
        return pd.DataFrame()

    return df[need + ["Volume"]].astype(float)


# ==========================================================
# Feature alignment
# ==========================================================
def _get_model_feature_names(model: XGBClassifier) -> List[str]:
    names = getattr(model, "feature_names_in_", None)
    if names is not None:
        return list(names)
    try:
        b = model.get_booster()
        if b is not None and b.feature_names is not None:
            return list(b.feature_names)
    except Exception:
        pass
    return []


def _align_features(model: XGBClassifier, X: pd.DataFrame) -> pd.DataFrame:
    want = _get_model_feature_names(model)
    if not want:
        return X.copy()
    cur = set(X.columns)
    for c in want:
        if c not in cur:
            X[c] = 0.0
    return X[want].copy()


# ==========================================================
# Mapping helpers
# ==========================================================
def _mapping_path(symbol: str) -> Path:
    return Path(settings.MODEL_DIR) / f"mapping_{symbol}.json"


def _load_mapping(symbol: str) -> Dict[int, int]:
    p = _mapping_path(symbol)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {int(k): int(v) for k, v in d.items()}
        except Exception:
            pass
    return {0: -1, 1: 0, 2: 1}


def _write_mapping(symbol: str, le: LabelEncoder) -> Dict[int, int]:
    mapping = {int(i): int(cls) for i, cls in enumerate(le.classes_)}
    _mapping_path(symbol).write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return mapping


# ==========================================================
# Sanity checks (detect collapse)
# ==========================================================
def _sanity_probs(P: np.ndarray, tag: str, min_std_mean: float = 1e-4) -> None:
    if P is None or len(P) == 0:
        raise RuntimeError(f"[SANITY] {tag}: empty proba")
    P = np.asarray(P, dtype=float)
    if P.ndim != 2 or P.shape[1] < 2:
        raise RuntimeError(f"[SANITY] {tag}: bad proba shape={getattr(P, 'shape', None)}")

    std_mean = float(P.std(axis=0).mean())
    mean_vec = P.mean(axis=0)
    w = np.argmax(P, axis=1)
    u, cnts = np.unique(w, return_counts=True)
    winner_counts = dict(zip(u.tolist(), cnts.tolist()))

    print(f"[SANITY] {tag}: P_std_mean={std_mean:.8f} winner_counts={winner_counts} P_mean={mean_vec}")

    # collapsed => almost constant proba everywhere
    if std_mean < min_std_mean:
        raise RuntimeError(f"[SANITY] model collapsed (P_std_mean={std_mean:.8f})")


def _balanced_sample_weight(y: np.ndarray) -> np.ndarray:
    """
    Returns weights with mean=1.0
    w_i = n / (K * count[y_i])
    This is much more stable than 1/count (which can be extremely small).
    """
    y = np.asarray(y, dtype=int)
    n = len(y)
    K = int(np.max(y)) + 1
    counts = np.bincount(y, minlength=K).astype(float)
    counts[counts < 1.0] = 1.0
    w_per_class = n / (K * counts)  # mean weight ~ 1
    w = w_per_class[y]
    return w.astype(float)


# ==========================================================
# Training (with staged fallback)
# ==========================================================
def train_and_save() -> Tuple[str, Dict]:
    symbol = _norm_symbol(os.environ.get("SYMBOL", "") or getattr(settings, "SYMBOL", "XAUUSDr"))
    horizon = int(getattr(settings, "TRAIN_HORIZON", 6))

    dfp = _load_prices_for_training(symbol)
    if dfp.empty:
        raise RuntimeError("no price data")

    dfn = pd.DataFrame(columns=["time", "impact", "currency"])

    X = make_features(dfp, dfn)
    y_raw = make_labels(dfp, horizon=horizon)

    both = pd.concat([X, y_raw.rename("y")], axis=1).dropna()
    if both.empty:
        raise RuntimeError("no training rows after concat/dropna")

    X2 = both.drop(columns=["y"]).astype(float)
    y2 = both["y"].astype(int)

    vc = y2.value_counts().to_dict()
    print("[TRAIN] label_counts:", vc)

    le = LabelEncoder()
    y_enc = le.fit_transform(y2.tolist())
    if len(set(y_enc)) < 2:
        raise RuntimeError("need at least two classes")

    n = len(X2)
    split = int(n * 0.80)
    if split < 2000:
        raise RuntimeError(f"not enough rows for time split: n={n}")

    X_train = X2.iloc[:split].copy()
    X_test = X2.iloc[split:].copy()
    y_train = np.asarray(y_enc[:split], dtype=int)
    y_test = np.asarray(y_enc[split:], dtype=int)

    sample_weight = _balanced_sample_weight(y_train)

    # Candidate stages — max_depth reduced (7→5) and early stopping added
    # to prevent catastrophic overfitting (train AUC 0.9997 vs test 0.52).
    stages = [
        dict(name="stage0", n_estimators=2000, max_depth=5, learning_rate=0.03, reg_lambda=2.0, tree_method="hist"),
        dict(name="stage1", n_estimators=2000, max_depth=4, learning_rate=0.05, reg_lambda=1.5, tree_method="hist"),
        dict(name="stage2", n_estimators=1500, max_depth=5, learning_rate=0.02, reg_lambda=2.5, tree_method="hist"),
        dict(name="stage3_exact", n_estimators=1000, max_depth=4, learning_rate=0.05, reg_lambda=2.0, tree_method="exact"),
    ]

    last_err: Optional[Exception] = None
    best_model: Optional[XGBClassifier] = None

    for st in stages:
        try:
            print(
                f"[TRAIN] {st['name']} params: "
                f"n_estimators={st['n_estimators']} max_depth={st['max_depth']} "
                f"lr={st['learning_rate']} reg_lambda={st['reg_lambda']} tree_method={st['tree_method']}"
            )

            model = XGBClassifier(
                n_estimators=int(st["n_estimators"]),
                max_depth=int(st["max_depth"]),
                learning_rate=float(st["learning_rate"]),
                subsample=0.75,
                colsample_bytree=0.75,
                min_child_weight=5,
                gamma=0.1,
                objective="multi:softprob",
                num_class=len(le.classes_),
                eval_metric="mlogloss",
                early_stopping_rounds=50,   # stop before memorizing noise
                random_state=42,
                tree_method=str(st["tree_method"]),
                n_jobs=1,
                reg_lambda=float(st["reg_lambda"]),
                reg_alpha=0.3,
                max_bin=256,
            )

            model.fit(
                X_train,
                y_train,
                sample_weight=sample_weight,
                eval_set=[(X_test, y_test)],
                verbose=100,
            )

            # sanity on test and train tail
            P_test = model.predict_proba(X_test.tail(2000))
            _sanity_probs(P_test, "test", min_std_mean=1e-4)

            P_tr = model.predict_proba(X_train.tail(2000))
            _sanity_probs(P_tr, "train_tail", min_std_mean=1e-4)

            best_model = model
            last_err = None
            break

        except Exception as e:
            print(f"[TRAIN] {st['name']} FAILED: {e}")
            last_err = e
            best_model = None

    if best_model is None:
        raise RuntimeError(f"All training stages failed. Last error: {last_err}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(settings.MODEL_DIR) / f"xgb_{symbol}_{ts}.bin"
    save_model_binary(best_model, out_path)
    set_active_model(out_path)
    _write_mapping(symbol, le)

    report = classification_report(y_test, best_model.predict(X_test), output_dict=True)
    return str(out_path), report


# ==========================================================
# Inference
# ==========================================================
def predict_direction(
    symbol: str,
    news_phase: str,
    news_impact: int,
    tech_dir: int,
    tech_strength: float,
    force: bool = False,
):
    symbol = _norm_symbol(symbol)
    model = load_active_model()
    if model is None:
        return "FLAT", 0.0, "no-model"

    dfp = _fetch_prices(symbol, lookback_days=5)
    if dfp.empty:
        return "FLAT", 0.0, "no-price"

    now = pd.Timestamp.utcnow()
    dfn = pd.DataFrame([{"time": now, "impact": int(news_impact), "currency": "USD"}])

    X = make_features(dfp, dfn)
    if X is None or X.empty:
        return "FLAT", 0.0, "no-features"

    X = _align_features(model, X)
    xlast = X.iloc[[-1]]

    try:
        probs_row = model.predict_proba(xlast)[0]
    except Exception:
        pred_raw = int(model.predict(xlast)[0])  # class_index
        mapping = _load_mapping(symbol)
        y_hat = int(mapping.get(pred_raw, 0))
        if y_hat > 0:
            return "BUY", 0.60, "ml-predict(no-proba)"
        if y_hat < 0:
            return "SELL", 0.60, "ml-predict(no-proba)"
        return "FLAT", 0.0, "ml-predict(no-proba)"

    mapping = _load_mapping(symbol)
    rev = {v: k for k, v in mapping.items()}
    idx_neg = rev.get(-1, 0)
    idx_flat = rev.get(0, 1)
    idx_pos = rev.get(1, 2)

    p_sell = float(probs_row[idx_neg]) if idx_neg < len(probs_row) else 0.0
    p_flat = float(probs_row[idx_flat]) if idx_flat < len(probs_row) else 0.0
    p_buy = float(probs_row[idx_pos]) if idx_pos < len(probs_row) else 0.0

    ml_dir = "BUY" if p_buy > p_sell else "SELL"
    p_dir = max(p_buy, p_sell)
    margin = abs(p_buy - p_sell)

    # gating (stable defaults)
    imp = int(news_impact)
    base_min_prob = 0.60
    base_min_margin = 0.06
    max_flat_allowed = 0.55

    if news_phase == "pre":
        if imp >= 3:
            base_min_prob = 0.72
            base_min_margin = 0.10
            max_flat_allowed = 0.45
        elif imp == 2:
            base_min_prob = 0.66
            base_min_margin = 0.08
            max_flat_allowed = 0.50
    elif news_phase == "post":
        if imp >= 3:
            base_min_prob = 0.64
            base_min_margin = 0.07
            max_flat_allowed = 0.52

    tech_strength_c = float(max(0.0, min(1.0, tech_strength)))
    tech_agree = (ml_dir == "BUY" and tech_dir > 0) or (ml_dir == "SELL" and tech_dir < 0)

    if tech_agree and tech_strength_c >= 0.60:
        base_min_prob = max(0.55, base_min_prob - 0.04)
        base_min_margin = max(0.04, base_min_margin - 0.02)

    if (not tech_agree) and tech_dir != 0 and tech_strength_c >= 0.70:
        base_min_prob = min(0.80, base_min_prob + 0.06)
        base_min_margin = min(0.14, base_min_margin + 0.04)

    abstain_reasons = []
    if p_flat > max_flat_allowed:
        abstain_reasons.append(f"p_flat>{max_flat_allowed:.2f}")
    if p_dir < base_min_prob:
        abstain_reasons.append(f"p_dir<{base_min_prob:.2f}")
    if margin < base_min_margin:
        abstain_reasons.append(f"margin<{base_min_margin:.2f}")

    if abstain_reasons and not (force and tech_dir != 0):
        why = (
            f"abstain: {','.join(abstain_reasons)}; "
            f"p=[sell:{p_sell:.2f}, flat:{p_flat:.2f}, buy:{p_buy:.2f}] "
            f"news={news_phase}-{imp} tech={tech_dir}@{tech_strength_c:.2f}"
        )
        return "FLAT", 0.0, why

    if force and tech_dir != 0:
        direction = "BUY" if tech_dir > 0 else "SELL"
        conf = 0.50
        why = (
            f"FORCED: tech_dir={tech_dir}@{tech_strength_c:.2f}; "
            f"ML(p_sell={p_sell:.2f}, p_flat={p_flat:.2f}, p_buy={p_buy:.2f}) "
            f"news={news_phase}-{imp}"
        )
        return direction, float(max(0.0, min(conf, 0.99))), why

    conf = p_dir * (1.0 - 0.35 * max(0.0, min(1.0, p_flat)))
    if tech_agree:
        conf += 0.03 * tech_strength_c
    conf = float(max(0.0, min(conf, 0.99)))

    rationale = (
        f"ML(p_sell={p_sell:.2f}, p_flat={p_flat:.2f}, p_buy={p_buy:.2f}, "
        f"p_dir={p_dir:.2f}, margin={margin:.2f}); "
        f"NEWS={news_phase}-{imp}; TECH={tech_dir}@{tech_strength_c:.2f}; "
        f"thr(p>={base_min_prob:.2f},m>={base_min_margin:.2f},flat<={max_flat_allowed:.2f})"
    )
    return ml_dir, conf, rationale


def switch_active_model(path: str):
    set_active_model(Path(path))
    return str(path)
