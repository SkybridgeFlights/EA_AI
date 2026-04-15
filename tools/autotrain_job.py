# tools/autotrain_job.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from hyperopt import hp, fmin, tpe, Trials, STATUS_OK
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, accuracy_score

from app.config import settings

try:
    from xgboost import XGBClassifier
except ImportError as e:
    print("[autotrain][FATAL] xgboost not installed:", e, file=sys.stderr)
    raise


# ============================= Paths / Settings =============================

ROOT = Path(getattr(settings, "ROOT", Path(__file__).resolve().parents[1])).resolve()

FEATURE_STORE_ROOT = Path(
    getattr(settings, "FEATURE_STORE_ROOT", ROOT / "data")
).resolve()

_fs_path_setting = getattr(settings, "FEATURE_STORE_PATH", None)
if _fs_path_setting:
    FEATURE_STORE_PATH = Path(_fs_path_setting).resolve()
else:
    FEATURE_STORE_PATH = (FEATURE_STORE_ROOT / "features.parquet").resolve()

MODELS_DIR = Path(getattr(settings, "MODELS_DIR", ROOT / "models")).resolve()
MODELS_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE_MODEL_FILE = Path(
    getattr(settings, "ACTIVE_MODEL_FILE", MODELS_DIR / "active_model.json")
).resolve()

MODEL_BIN_FILE = Path(
    getattr(settings, "MODEL_BIN_FILE", MODELS_DIR / "xgb_model.bin")
).resolve()

# العتبة "المثالية" للتدريب الجدي، لكن سنسمح بالتدريب حتى لو تحتها مع تحذير
MIN_ROWS_FOR_TRAIN = int(getattr(settings, "MIN_ROWS_FOR_TRAIN", 200))


# ============================= Helpers =============================

def _log(msg: str) -> None:
    print(f"[autotrain] {msg}")


def _load_feature_store() -> pd.DataFrame:
    """
    يحاول تحميل Feature Store من FEATURE_STORE_PATH
    مع fallback إلى:
      - ROOT/data/features.parquet
      - ROOT/runtime/features/features.parquet
    """
    candidates = []

    candidates.append(FEATURE_STORE_PATH)
    candidates.append((ROOT / "data" / "features.parquet").resolve())
    candidates.append((ROOT / "runtime" / "features" / "features.parquet").resolve())

    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    _log("FEATURE_STORE search order:")
    for c in unique_candidates:
        _log(f"  - {c}")

    for path in unique_candidates:
        if path.exists():
            _log(f"loading feature store from: {path}")
            try:
                df = pd.read_parquet(path)
                _log(f"loaded feature store shape={df.shape}")
                return df
            except Exception as e:
                _log(f"[WARN] failed to read {path}: {e}")

    _log("feature store file not found in any candidate path. returning empty DataFrame.")
    return pd.DataFrame()


def _prepare_xy(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    تحضير X و y للتدريب:

    - target = 1 إذا R > 0 ، وإلا 0
    - نستخدم الأعمدة الرقمية فقط كـ features
    - لا نحذف الصفوف بسبب NaN في أي عمود:
        * نحذف فقط الصفوف التي تكون فيها كل الفيتشرات NaN
        * بعد ذلك نملأ NaN بالـ median لكل عمود (أو 0 إذا تعذر)
    """
    if "R" not in df.columns:
        raise RuntimeError("feature store does not contain column 'R' (target).")

    # الهدف
    R = pd.to_numeric(df["R"], errors="coerce")
    y = (R > 0.0).astype("int8")

    # حذف الصفوف التي ليس لها R صالح
    mask_R = R.notna()
    df = df.loc[mask_R].copy()
    y = y.loc[mask_R].reset_index(drop=True)

    # استبدال الـ inf/ -inf بـ NaN
    df = df.replace([np.inf, -np.inf], np.nan)

    # حذف الأعمدة غير المفيدة / النصية الثقيلة
    drop_cols = [
        "time",
        "regime",
        "news_bucket",
        "news_bucket_clean",
        "comment",
        "why",
        "ai_reason",
        "news_title",
        "news_text",
    ]
    for c in drop_cols:
        if c in df.columns:
            df.drop(columns=[c], inplace=True, errors="ignore")

    # نأخذ الأعمدة الرقمية فقط
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if "R" in num_cols:
        num_cols.remove("R")

    if not num_cols:
        raise RuntimeError("no numeric feature columns found for training.")

    X = df[num_cols].copy()

    # حذف الصفوف التي تحتوي على كل القيم NaN في الفيتشرات
    mask_any = X.notna().any(axis=1)
    X = X.loc[mask_any].reset_index(drop=True)
    y = y.loc[mask_any].reset_index(drop=True)

    if X.empty:
        _log("after removing rows with all-NaN features, X is empty.")
        return X, y  # سيُكتشف لاحقاً في main

    # ملء NaN بالبداية بـ median لكل عمود، وإذا كان كله NaN نستخدم 0.0
    medians = {}
    for c in num_cols:
        col = X[c]
        med = col.median()
        if not np.isfinite(med):
            med = 0.0
        medians[c] = float(med)

    X = X.fillna(medians)

    _log(
        f"final training matrix: X.shape={X.shape}, y.shape={y.shape}, "
        f"num_features={len(num_cols)}"
    )
    return X, y


# ============================= Hyperopt Space =============================

space = {
    "max_depth": hp.quniform("max_depth", 3, 10, 1),
    "learning_rate": hp.loguniform("learning_rate", np.log(0.01), np.log(0.3)),
    "subsample": hp.uniform("subsample", 0.5, 1.0),
    "colsample_bytree": hp.uniform("colsample_bytree", 0.5, 1.0),
    "min_child_weight": hp.quniform("min_child_weight", 1, 10, 1),
    "gamma": hp.loguniform("gamma", np.log(1e-4), np.log(10.0)),
    "reg_lambda": hp.loguniform("reg_lambda", np.log(1e-4), np.log(10.0)),
    "reg_alpha": hp.loguniform("reg_alpha", np.log(1e-4), np.log(10.0)),
    "n_estimators": hp.quniform("n_estimators", 50, 400, 10),
}


def _build_model(params: Dict[str, Any]) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=int(params["n_estimators"]),
        max_depth=int(params["max_depth"]),
        learning_rate=float(params["learning_rate"]),
        subsample=float(params["subsample"]),
        colsample_bytree=float(params["colsample_bytree"]),
        min_child_weight=int(params["min_child_weight"]),
        gamma=float(params["gamma"]),
        reg_lambda=float(params["reg_lambda"]),
        reg_alpha=float(params["reg_alpha"]),
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=int(os.getenv("XGB_N_JOBS", "4")),
        random_state=42,
    )


# ============================= Training Loop =============================

def main() -> None:
    print("============================================")
    print("[autotrain] EA Self-Training Job (XGBoost)")
    print("============================================")
    _log(f"ROOT={ROOT}")
    _log(f"FEATURE_STORE_ROOT={FEATURE_STORE_ROOT}")
    _log(f"FEATURE_STORE_PATH={FEATURE_STORE_PATH}")
    _log(f"ACTIVE_MODEL_FILE={ACTIVE_MODEL_FILE}")
    _log(f"MODEL_BIN_FILE={MODEL_BIN_FILE}")
    _log(f"MIN_ROWS_FOR_TRAIN={MIN_ROWS_FOR_TRAIN}")
    print("--------------------------------------------")

    df = _load_feature_store()
    if df.empty:
        _log("feature store is EMPTY. no training will be performed.")
        _log("leave EA running to collect trades (JSONL/feature store) and rerun later.")
        return

    _log(f"raw feature store shape={df.shape}")

    try:
        X, y = _prepare_xy(df)
    except Exception as e:
        _log(f"[FATAL] prepare_xy failed: {e}")
        return

    n_samples = X.shape[0]
    if n_samples == 0:
        _log("after preprocessing, no rows left for training. collect more trades.")
        return

    # سياسة جديدة:
    # - إذا n_samples < 5 -> لا ندرب إطلاقاً (شويّة جداً)
    # - إذا 5 <= n_samples < MIN_ROWS_FOR_TRAIN -> تحذير لكن نكمل التدريب (نماذج صغيرة)
    if n_samples < 5:
        _log(
            f"too few rows for any meaningful training: n_samples={n_samples} < 5. "
            "no training will be performed."
        )
        return

    if n_samples < MIN_ROWS_FOR_TRAIN:
        _log(
            f"WARNING: n_samples={n_samples} < MIN_ROWS_FOR_TRAIN={MIN_ROWS_FOR_TRAIN}. "
            "will still train a small model (experimental)."
        )

    X_train, X_val, y_train, y_val = train_test_split(
        X,
        y,
        test_size=0.2 if n_samples > 10 else 0.33,
        random_state=42,
        shuffle=True,
        stratify=y if len(np.unique(y)) > 1 else None,
    )

    _log(f"train shape={X_train.shape}, val shape={X_val.shape}")

    def objective(params: Dict[str, Any]) -> Dict[str, Any]:
        model = _build_model(params)
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_val)[:, 1]

        try:
            auc = roc_auc_score(y_val, proba)
        except ValueError:
            auc = 0.5

        preds = (proba >= 0.5).astype(int)
        acc = accuracy_score(y_val, preds)

        loss = -float(auc)

        return {
            "loss": loss,
            "status": STATUS_OK,
            "auc": float(auc),
            "acc": float(acc),
        }

    trials = Trials()
    max_evals = int(os.getenv("AUTOTRAIN_MAX_EVALS", "30" if n_samples < 50 else "40"))

    _log(f"starting hyperopt: max_evals={max_evals}")
    best = fmin(
        fn=objective,
        space=space,
        algo=tpe.suggest,
        max_evals=max_evals,
        trials=trials,
        rstate=np.random.default_rng(42),
        show_progressbar=True,
    )

    best_result = min(trials.results, key=lambda r: r["loss"])
    best_auc = float(best_result.get("auc", 0.0))
    best_acc = float(best_result.get("acc", 0.0))

    _log(f"best hyperopt params={best}")
    _log(f"best AUC={best_auc:.4f}, ACC={best_acc:.4f}")

    best_params = dict(best)
    model = _build_model(best_params)
    model.fit(X, y)

    _log(f"saving model binary -> {MODEL_BIN_FILE}")
    MODEL_BIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_BIN_FILE))

    meta: Dict[str, Any] = {
        "model_type": "xgboost_binary_classifier",
        "auc": best_auc,
        "acc": best_acc,
        "rows": int(n_samples),
        "n_features": int(X.shape[1]),
        "features": list(X.columns),
        "params": best_params,
    }

    _log(f"writing active model meta -> {ACTIVE_MODEL_FILE}")
    ACTIVE_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVE_MODEL_FILE.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    _log("training job finished successfully.")


if __name__ == "__main__":
    main()
