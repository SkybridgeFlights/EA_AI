# tools/walk_forward_xgb.py
# Walk-Forward (rolling) training + evaluation + signal export for MT5 backtest parity
# - Reads MT5 CSV once
# - For each fold: train on past window, test on next window
# - Saves model + mapping per fold
# - Generates replay signals CSV for TEST window only (for honest backtest use)
# - Produces a summary CSV report

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

from app.config import settings
from app.ml.features import make_features, make_labels
from app.ml.registry import save_model_binary


# -----------------------------
# IO: MT5 CSV -> OHLCV DataFrame
# -----------------------------
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


def load_mt5_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    df = pd.read_csv(p, sep=None, engine="python")
    df.columns = [c.strip() for c in df.columns]
    cols_l = {c.lower(): c for c in df.columns}

    # time detection
    if "time" in cols_l:
        dt = pd.to_datetime(df[cols_l["time"]], utc=True, errors="coerce")
    elif "dt" in cols_l:
        dt = pd.to_datetime(df[cols_l["dt"]], utc=True, errors="coerce")
    elif "date" in cols_l and "time" in cols_l:
        dt = pd.to_datetime(df[cols_l["date"]].astype(str) + " " + df[cols_l["time"]].astype(str), utc=True, errors="coerce")
    else:
        dt = pd.to_datetime(df.iloc[:, 0], utc=True, errors="coerce")

    df = df.copy()
    df["dt"] = dt
    df = df.dropna(subset=["dt"]).sort_values("dt").drop_duplicates("dt", keep="last").set_index("dt")

    # normalize names
    rename = {}
    for c in df.columns:
        cl = c.strip().lower()
        if cl == "open":
            rename[c] = "Open"
        elif cl == "high":
            rename[c] = "High"
        elif cl == "low":
            rename[c] = "Low"
        elif cl == "close":
            rename[c] = "Close"
        elif cl in ("volume", "tick_volume", "real_volume", "tickvolume", "realvolume"):
            rename[c] = "Volume"
    df = df.rename(columns=rename)

    for r in ["Open", "High", "Low", "Close"]:
        if r not in df.columns:
            raise RuntimeError(f"Missing column {r}. Found: {list(df.columns)}")

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).astype(float)
    df = _ensure_utc_index(df)
    return df


# -----------------------------
# Fold slicing
# -----------------------------
@dataclass
class Fold:
    i: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def month_add(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    # safe month add
    return (ts + pd.DateOffset(months=int(months))).to_pydatetime().replace(tzinfo=None)


def build_folds(
    start: pd.Timestamp,
    end: pd.Timestamp,
    train_months: int,
    test_months: int,
    step_months: int,
) -> Tuple[Fold, ...]:
    folds = []
    i = 0
    t0 = start

    # define first train window: [t0, t0+train_months)
    while True:
        train_start = t0
        train_end = pd.Timestamp(month_add(train_start, train_months), tz="UTC")
        test_start = train_end
        test_end = pd.Timestamp(month_add(test_start, test_months), tz="UTC")

        if test_end > end:
            break

        folds.append(Fold(i=i, train_start=train_start, train_end=train_end, test_start=test_start, test_end=test_end))
        i += 1

        # slide
        t0 = pd.Timestamp(month_add(t0, step_months), tz="UTC")

    return tuple(folds)


# -----------------------------
# Model train / predict
# -----------------------------
def fit_xgb_multiclass(X_train: pd.DataFrame, y_train_raw: pd.Series) -> Tuple[XGBClassifier, LabelEncoder]:
    le = LabelEncoder()
    y_train = le.fit_transform(y_train_raw.astype(int).tolist())

    # class balancing
    counts = np.bincount(y_train)
    counts = np.maximum(counts, 1)
    w_per_class = 1.0 / counts
    sample_weight = w_per_class[y_train]

    model = XGBClassifier(
        n_estimators=600,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        objective="multi:softprob",
        num_class=len(le.classes_),
        eval_metric="mlogloss",
        random_state=42,
        tree_method="hist",
        n_jobs=-1,
        reg_lambda=1.2,
        reg_alpha=0.2,
    )

    # IMPORTANT: no early_stopping_rounds to avoid wrapper mismatch
    model.fit(X_train, y_train, sample_weight=sample_weight, verbose=False)
    return model, le


def probs_to_signal(probs_row: np.ndarray, mapping: Dict[int, int], min_conf: float) -> Dict:
    """
    mapping: class_index -> (-1/0/+1)
    returns: dict with dir/conf/p_buy/p_sell/p_flat/margin
    """
    rev = {v: k for k, v in mapping.items()}
    idx_sell = rev.get(-1, 0)
    idx_flat = rev.get(0, 1)
    idx_buy = rev.get(1, 2)

    p_sell = float(probs_row[idx_sell]) if idx_sell < len(probs_row) else 0.0
    p_flat = float(probs_row[idx_flat]) if idx_flat < len(probs_row) else 0.0
    p_buy = float(probs_row[idx_buy]) if idx_buy < len(probs_row) else 0.0

    # direction is strongest between buy/sell
    if p_buy >= p_sell:
        dir_ = 1
        conf = p_buy
    else:
        dir_ = -1
        conf = p_sell

    margin = abs(p_buy - p_sell)

    # apply min_conf: if not passed -> FLAT
    if conf < float(min_conf):
        dir_ = 0
        conf = 0.0

    return {
        "dir": int(dir_),
        "conf": float(conf),
        "p_buy": float(p_buy),
        "p_sell": float(p_sell),
        "p_flat": float(p_flat),
        "margin": float(margin),
    }


# -----------------------------
# Main
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True, help="MT5 CSV path (time/open/high/low/close/volume...)")
    ap.add_argument("--symbol", required=True, help="Symbol tag e.g. XAUUSDr")
    ap.add_argument("--outdir", default="reports/walk_forward", help="Output base dir")
    ap.add_argument("--train_months", type=int, default=12)
    ap.add_argument("--test_months", type=int, default=1)
    ap.add_argument("--step_months", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--min_conf", type=float, default=0.55)
    ap.add_argument("--copy_to_mt5_common", default="", help="If set: path to Terminal\\Common\\Files (or leave empty)")
    ap.add_argument("--tf_tag", default="M15", help="Tag only for filenames (M15/M30...)")
    args = ap.parse_args()

    prices_path = args.prices
    symbol = args.symbol

    dfp = load_mt5_csv(prices_path)
    if dfp.empty:
        raise SystemExit("No prices loaded.")

    start = dfp.index.min()
    end = dfp.index.max()

    folds = build_folds(
        start=start,
        end=end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    if not folds:
        raise SystemExit("No folds produced. Reduce train_months/test_months or ensure enough data.")

    # output root
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    root = Path(args.outdir) / f"wf_{symbol}_{args.tf_tag}_{ts}"
    root.mkdir(parents=True, exist_ok=True)

    # build full features/labels once (then slice by time)
    dfn = pd.DataFrame(columns=["time", "impact", "currency"])
    X_all = make_features(dfp, dfn)
    y_all = make_labels(dfp, horizon=int(args.horizon))

    both = pd.concat([X_all, y_all.rename("y")], axis=1).dropna()
    if both.empty:
        raise SystemExit("No training rows after feature/label build.")

    X_all = both.drop(columns=["y"])
    y_all = both["y"].astype(int)

    summary_rows = []

    for fold in folds:
        fdir = root / f"fold_{fold.i:03d}"
        fdir.mkdir(parents=True, exist_ok=True)

        # time masks
        m_train = (X_all.index >= fold.train_start) & (X_all.index < fold.train_end)
        m_test = (X_all.index >= fold.test_start) & (X_all.index < fold.test_end)

        X_train = X_all.loc[m_train].copy()
        y_train = y_all.loc[m_train].copy()
        X_test = X_all.loc[m_test].copy()
        y_test = y_all.loc[m_test].copy()

        if len(X_train) < 1000 or len(X_test) < 200:
            # skip tiny folds
            continue

        # fit model
        model, le = fit_xgb_multiclass(X_train, y_train)

        # mapping
        mapping = {int(i): int(cls) for i, cls in enumerate(le.classes_)}  # class_index -> -1/0/+1
        (fdir / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")

        # save model
        model_path = fdir / f"xgb_{symbol}_{args.tf_tag}_fold{fold.i:03d}.bin"
        save_model_binary(model, model_path)

        # evaluate on TEST (encoded domain)
        y_test_enc = le.transform(y_test.astype(int).tolist())
        y_pred_enc = model.predict(X_test)
        acc = float(accuracy_score(y_test_enc, y_pred_enc))

        rep = classification_report(y_test_enc, y_pred_enc, output_dict=True, zero_division=0)
        cm = confusion_matrix(y_test_enc, y_pred_enc).tolist()

        (fdir / "metrics.json").write_text(
            json.dumps({"accuracy": acc, "report": rep, "confusion_matrix": cm}, indent=2),
            encoding="utf-8",
        )

        # generate replay signals for TEST only
        probs = model.predict_proba(X_test)
        rows = []
        for dt, pr in zip(X_test.index, probs):
            s = probs_to_signal(pr, mapping=mapping, min_conf=args.min_conf)
            rows.append(
                {
                    "dt": dt.isoformat(),
                    "dir": s["dir"],
                    "conf": s["conf"],
                    "p_buy": s["p_buy"],
                    "p_sell": s["p_sell"],
                    "p_flat": s["p_flat"],
                    "margin": s["margin"],
                }
            )

        sig_df = pd.DataFrame(rows)
        sig_path = fdir / f"signals_{symbol}_{args.tf_tag}_fold{fold.i:03d}.csv"
        sig_df.to_csv(sig_path, index=False)

        # counts
        vc = sig_df["dir"].value_counts().to_dict()
        eligible = int((sig_df["dir"] != 0).sum())

        summary_rows.append(
            {
                "fold": fold.i,
                "train_start": fold.train_start.isoformat(),
                "train_end": fold.train_end.isoformat(),
                "test_start": fold.test_start.isoformat(),
                "test_end": fold.test_end.isoformat(),
                "train_rows": int(len(X_train)),
                "test_rows": int(len(X_test)),
                "accuracy": acc,
                "eligible_signals": eligible,
                "dir_-1": int(vc.get(-1, 0)),
                "dir_0": int(vc.get(0, 0)),
                "dir_1": int(vc.get(1, 0)),
                "model_path": str(model_path),
                "signals_path": str(sig_path),
            }
        )

        # optionally copy to MT5 Common\Files for backtest
        if args.copy_to_mt5_common:
            common = Path(args.copy_to_mt5_common)
            dst_dir = common / "ai_signals"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / sig_path.name
            try:
                dst.write_bytes(sig_path.read_bytes())
            except Exception as e:
                print("[WARN] copy_to_mt5_common failed:", e)

        print(
            f"[FOLD {fold.i:03d}] "
            f"train={fold.train_start.date()}..{fold.train_end.date()} "
            f"test={fold.test_start.date()}..{fold.test_end.date()} "
            f"acc={acc:.4f} eligible={eligible} "
            f"dir(-1/1)={int(vc.get(-1,0))}/{int(vc.get(1,0))}"
        )

    if not summary_rows:
        raise SystemExit("All folds were skipped (too few rows). Adjust windows.")

    summary = pd.DataFrame(summary_rows)
    summary_path = root / "summary.csv"
    summary.to_csv(summary_path, index=False)

    print("\nDONE.")
    print("Report dir:", str(root))
    print("Summary:", str(summary_path))


if __name__ == "__main__":
    main()
