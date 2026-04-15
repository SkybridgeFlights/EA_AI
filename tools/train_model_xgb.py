# C:\EA_AI\tools\train_model_xgb.py
# -*- coding: utf-8 -*-
"""
Train XGBoost model on Feature Store.

التشغيل (من داخل C:\EA_AI):

    python -m tools.build_feature_store
    python -m tools.train_model_xgb

يتطلب:
    pip install xgboost scikit-learn
"""

from __future__ import annotations

import os
import json
import glob
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    ROOT = Path(__file__).resolve().parents[1]
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(str(env_path))
except ImportError:
    pass

from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, accuracy_score

try:
    from xgboost import XGBClassifier
except ImportError:
    XGBClassifier = None
    print("[train_model_xgb] WARNING: xgboost غير مثبت. ثبّته قبل تشغيل التدريب.")


# ---------------------------------------------------------------------
# مسارات
# ---------------------------------------------------------------------
def _get_paths():
    feature_store_root = os.getenv("FEATURE_STORE_ROOT", "C:/EA_AI/data")
    artifacts_dir = os.getenv("ARTIFACTS_DIR", "C:/EA_AI/artifacts")
    model_dir = os.getenv("MODEL_DIR", "C:/EA_AI/models")
    active_model_file = os.getenv(
        "ACTIVE_MODEL_FILE", "C:/EA_AI/models/active_model.json"
    )

    os.makedirs(feature_store_root, exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)

    return Path(feature_store_root), Path(artifacts_dir), Path(model_dir), Path(active_model_file)


def _find_latest_feature_store(feature_store_root: Path, artifacts_dir: Path) -> Path | None:
    """
    يبحث عن أحدث ملف feature_store_*.parquet:
      1) أولاً في FEATURE_STORE_ROOT (مثل C:\EA_AI\data)
      2) إن لم يجد، يبحث في ARTIFACTS_DIR
    """
    def _glob_latest(folder: Path) -> Path | None:
        pattern = str(folder / "feature_store_*.parquet")
        files = glob.glob(pattern)
        if not files:
            return None
        files_sorted = sorted(files, key=lambda p: os.path.getmtime(p))
        return Path(files_sorted[-1])

    p = _glob_latest(feature_store_root)
    if p is not None:
        print(f"[train_model_xgb] استخدام Feature Store من FEATURE_STORE_ROOT: {p}")
        return p

    p = _glob_latest(artifacts_dir)
    if p is not None:
        print(f"[train_model_xgb] استخدام Feature Store من ARTIFACTS_DIR: {p}")
        return p

    print("[train_model_xgb] لا يوجد أي feature_store_*.parquet في FEATURE_STORE_ROOT ولا ARTIFACTS_DIR.")
    return None


# ---------------------------------------------------------------------
# تحميل الـ Feature Store وتجهيز الداتا
# ---------------------------------------------------------------------
def _load_feature_store(path: Path) -> pd.DataFrame:
    print(f"[train_model_xgb] قراءة Parquet: {path}")
    df = pd.read_parquet(path)
    print(f"[train_model_xgb] rows={len(df)}, cols={len(df.columns)}")
    return df


def _infer_target(df: pd.DataFrame) -> tuple[pd.Series, str, str]:
    """
    يحاول استنتاج الهدف (y) من الأعمدة الموجودة.

    يعيد:
        y (Series 0/1), used_target_col, source_type ('y_win' أو 'R' أو 'profit')
    """
    cols = set(df.columns)

    # 1) إذا وجد y_win مباشرة
    if "y_win" in cols:
        y = df["y_win"].astype("float32")
        print("[train_model_xgb] target = y_win (موجودة جاهزة في Feature Store)")
        return y, "y_win", "y_win"

    # 2) نحاول أعمدة R (R-multiple)
    r_candidates = [
        "y_R", "R", "r", "r_multiple", "R_multiple", "r_mult", "R_mult"
    ]
    for c in r_candidates:
        if c in cols:
            r = pd.to_numeric(df[c], errors="coerce")
            y = (r > 0).astype("float32")
            print(f"[train_model_xgb] target = win/loss محسوبة من العمود R: {c}")
            return y, c, "R"

    # 3) نحاول أعمدة profit / pnl
    p_candidates = [
        "profit_r", "profit", "pnl", "net_profit", "netPnl", "pnl_r"
    ]
    for c in p_candidates:
        if c in cols:
            p = pd.to_numeric(df[c], errors="coerce")
            y = (p > 0).astype("float32")
            print(f"[train_model_xgb] target = win/loss محسوبة من العمود profit/pnl: {c}")
            return y, c, "profit"

    # فشل في إيجاد هدف
    print("[train_model_xgb][ERROR] لم أجد أي عمود مناسب لبناء الهدف (y_win).")
    print("[train_model_xgb] الأعمدة المتاحة في Feature Store:")
    print(sorted(list(cols)))
    raise ValueError(
        "لا أستطيع استنتاج الهدف y من الأعمدة الحالية. "
        "تأكد أن Feature Store يحوي y_win أو عمود R أو profit."
    )


def _prepare_xy(df: pd.DataFrame):
    """
    يحضّر X, y من Feature Store بشكل ديناميكي.
    - الهدف: y_win (مباشر) أو مشتق من R أو profit.
    - الميزات: كل الأعمدة الرقمية باستثناء:
        time_ts, symbol_norm, الهدف، وعمود R/profit المستخدم للبناء.
    """
    y, target_col, target_source = _infer_target(df)

    # الأعمدة التي لا نستخدمها كـ features
    drop_cols = {"time_ts", "symbol_norm"}
    drop_cols.add(target_col)   # العمود الذي استنتجنا منه الهدف

    # لو كان لدينا نسخة أخرى من R/profit يمكن استبعادها أيضاً
    if target_source == "R" and "y_R" in df.columns:
        drop_cols.add("y_R")
    if target_source == "profit" and "profit_r" in df.columns:
        drop_cols.add("profit_r")

    drop_cols = {c for c in drop_cols if c in df.columns}

    numeric_cols = [
        c
        for c in df.columns
        if c not in drop_cols and pd.api.types.is_numeric_dtype(df[c])
    ]

    if not numeric_cols:
        raise ValueError(
            "لا توجد أعمدة رقمية مناسبة للاستخدام كـ features بعد استبعاد الأعمدة الهدف/الوقت."
        )

    # نأخذ الأعمدة الرقمية ونملأ أي NaN بالقيم 0.0 بدل إسقاط الصفوف
    X = df[numeric_cols].astype("float32")
    X = X.fillna(0.0)

    # الفلترة الوحيدة: إزالة الصفوف التي فيها y NaN (غالبًا لا يوجد أصلاً)
    valid_mask = ~y.isna()
    X = X[valid_mask]
    y = y[valid_mask]

    print(
        f"[train_model_xgb] بعد تجهيز البيانات: rows={len(X)}, features={len(numeric_cols)}, "
        f"target_col={target_col}, source={target_source}"
    )
    return X, y, numeric_cols


# ---------------------------------------------------------------------
# تدريب النموذج
# ---------------------------------------------------------------------
def _train_xgb(X: pd.DataFrame, y: pd.Series):
    if XGBClassifier is None:
        raise RuntimeError("xgboost غير مثبت.")

    if len(X) == 0:
        raise ValueError("لا توجد أي صفوف صالحة بعد تجهيز البيانات (rows==0).")

    if len(X) < 30:
        print(
            f"[train_model_xgb] WARNING: عدد الصفوف {len(X)} قليل ({len(X)}). "
            "النتائج تجريبية."
        )

    X_train, X_val, y_train, y_val = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y if len(y.unique()) > 1 else None,
    )

    model = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        n_jobs=-1,
        tree_method="hist",
    )

    print("[train_model_xgb] بدء التدريب ...")
    model.fit(X_train, y_train)

    y_proba = model.predict_proba(X_val)[:, 1]
    y_pred = (y_proba >= 0.5).astype("float32")

    try:
        auc = roc_auc_score(y_val, y_proba)
    except Exception:
        auc = float("nan")

    acc = accuracy_score(y_val, y_pred)

    metrics = {
        "n_rows": int(len(X)),
        "n_features": int(X.shape[1]),
        "auc": float(auc) if np.isfinite(auc) else None,
        "accuracy": float(acc),
        "class_balance": {
            "pos": float((y == 1).mean()),
            "neg": float((y == 0).mean()),
        },
    }

    print(
        f"[train_model_xgb] metrics: "
        f"rows={metrics['n_rows']} feats={metrics['n_features']} "
        f"auc={metrics['auc']} acc={metrics['accuracy']}"
    )

    return model, metrics


# ---------------------------------------------------------------------
# حفظ النموذج وتحديث active_model.json
# ---------------------------------------------------------------------
def _save_model(model, model_dir: Path, symbol_tag: str) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"xgb_{symbol_tag}_{ts}.bin"
    out_path = model_dir / fname
    model.save_model(str(out_path))
    print(f"[train_model_xgb] saved model: {out_path}")
    return out_path


def _update_active_model(
    active_model_file: Path,
    model_path: Path,
    feature_store_path: Path,
    feature_cols: list[str],
    metrics: dict,
):
    meta = {
        "model_type": "xgboost_binary_classifier",
        "symbol": os.getenv("SYMBOL", "XAUUSD"),
        "trained_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "model_path": str(model_path),
        "feature_store_path": str(feature_store_path),
        "features": feature_cols,
        "metrics": metrics,
        "version": "xgb_v1",
    }

    active_model_file.parent.mkdir(parents=True, exist_ok=True)
    active_model_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[train_model_xgb] updated ACTIVE_MODEL_FILE: {active_model_file}")


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------
def main() -> int:
    feature_store_root, artifacts_dir, model_dir, active_model_file = _get_paths()

    fs_path = _find_latest_feature_store(feature_store_root, artifacts_dir)
    if fs_path is None:
        return 1

    df = _load_feature_store(fs_path)
    if df.empty:
        print("[train_model_xgb] Feature Store فارغ. لا يمكن التدريب.")
        return 1

    try:
        X, y, feature_cols = _prepare_xy(df)
    except Exception as e:
        print("[train_model_xgb][ERROR] أثناء تجهيز الداتا:", e)
        return 1

    try:
        model, metrics = _train_xgb(X, y)
    except Exception as e:
        print("[train_model_xgb][ERROR] أثناء التدريب:", e)
        return 1

    train_symbols = os.getenv("TRAIN_SYMBOLS") or os.getenv("SYMBOL", "XAUUSD")
    sym_tag = "_".join(sorted([s.strip().upper() for s in train_symbols.split(",") if s.strip()]))

    model_path = _save_model(model, model_dir, sym_tag)
    _update_active_model(active_model_file, model_path, fs_path, feature_cols, metrics)

    print("[train_model_xgb] DONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
