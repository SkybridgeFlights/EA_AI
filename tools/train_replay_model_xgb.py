# tools/train_replay_model_xgb.py
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from xgboost import XGBClassifier
from sklearn.metrics import classification_report, f1_score
from sklearn.preprocessing import LabelEncoder

from app.config import settings
from app.ml.features import make_features, make_labels
from app.ml.model import _align_features

def read_mt5_prices_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    df = pd.read_csv(p, sep=None, engine="python")
    df.columns = [c.strip() for c in df.columns]

    # time parsing (MT5 export script يكتب time كـ نص)
    if "time" in df.columns:
        dt = pd.to_datetime(df["time"], errors="coerce", utc=True)
        df = df.drop(columns=["time"])
        df.insert(0, "dt", dt)
    elif "Time" in df.columns:
        dt = pd.to_datetime(df["Time"], errors="coerce", utc=True)
        df = df.drop(columns=["Time"])
        df.insert(0, "dt", dt)
    else:
        # fallback: أول عمود
        dt = pd.to_datetime(df.iloc[:, 0], errors="coerce", utc=True)
        df = df.drop(columns=[df.columns[0]])
        df.insert(0, "dt", dt)

    df = df.dropna(subset=["dt"]).copy()
    df = df.sort_values("dt").drop_duplicates("dt", keep="last")
    df = df.set_index("dt")

    # توحيد أسماء الأعمدة
    rename_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open": rename_map[c] = "Open"
        elif cl == "high": rename_map[c] = "High"
        elif cl == "low":  rename_map[c] = "Low"
        elif cl == "close":rename_map[c] = "Close"
        elif cl in ("volume", "tick_volume", "tickvol", "tickvol."): rename_map[c] = "Volume"
    df = df.rename(columns=rename_map)

    need = ["Open", "High", "Low", "Close"]
    for c in need:
        if c not in df.columns:
            raise ValueError(f"Missing column {c}. Found={list(df.columns)}")

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df.astype(float)

def time_split_index(index: pd.DatetimeIndex, valid_ratio: float) -> int:
    n = len(index)
    cut = int(n * (1.0 - valid_ratio))
    cut = max(1000, min(cut, n - 1000))
    return cut

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True, help="MT5 OHLCV CSV")
    ap.add_argument("--symbol", required=True, help="Symbol (e.g. XAUUSDr)")
    ap.add_argument("--horizon", type=int, default=6, help="Label horizon in bars (M15: 6=90min)")
    ap.add_argument("--valid_ratio", type=float, default=0.2, help="Validation ratio by time")
    ap.add_argument("--out_model", default="", help="Output model path (.bin). Default models/replay_xgb_SYMBOL_M15.bin")
    args = ap.parse_args()

    dfp = read_mt5_prices_csv(args.prices)

    # أخبار فارغة الآن (لاحقًا يمكن تغذيتها من calendar)
    df_news = pd.DataFrame(columns=["time", "impact", "currency"])

    X = make_features(dfp, df_news)
    y = make_labels(dfp, horizon=int(args.horizon))

    # محاذاة
    X = X.loc[y.index]
    # حذف آخر horizon (labels فيها shift)
    mask = ~y.isna()
    X = X.loc[mask]
    y = y.loc[mask].astype(int)

    # Label encode إلى 0/1/2
    le = LabelEncoder()
    y_enc = le.fit_transform(y.values)  # classes will be [-1,0,1] لكن مرمزة

    # time split
    cut = time_split_index(X.index, float(args.valid_ratio))
    X_tr, X_va = X.iloc[:cut].copy(), X.iloc[cut:].copy()
    y_tr, y_va = y_enc[:cut], y_enc[cut:]

    # class weights (balanced)
    classes, counts = np.unique(y_tr, return_counts=True)
    freq = counts / counts.sum()
    w = {int(c): float(1.0 / (f + 1e-12)) for c, f in zip(classes, freq)}
    sample_w = np.array([w[int(c)] for c in y_tr], dtype=float)

    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(le.classes_),
        n_estimators=450,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_alpha=0.2,
        reg_lambda=1.2,
        tree_method="hist",
        random_state=42,
        n_jobs=-1,
        eval_metric="mlogloss",
    )

    model.fit(X_tr, y_tr, sample_weight=sample_w)

    # تقييم
    proba = model.predict_proba(X_va)
    pred = np.argmax(proba, axis=1)
    f1m = f1_score(y_va, pred, average="macro")
    print("VALID macro-F1:", float(f1m))
    print(classification_report(y_va, pred))

    # حفظ الموديل
    out_model = args.out_model.strip()
    if not out_model:
        out_model = str(Path(settings.MODEL_DIR) / f"replay_xgb_{args.symbol}_M15.bin")

    outp = Path(out_model)
    outp.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, outp)

    # حفظ mapping class_index -> {-1,0,1} حسب le.classes_
    mapping = {int(i): int(v) for i, v in enumerate(le.classes_.tolist())}
    mp = Path(settings.MODEL_DIR) / f"mapping_{args.symbol}.json"
    mp.write_text(json.dumps(mapping, indent=2), encoding="utf-8")

    # تفعيل Replay Model
    from app.ml.replay_registry import set_active_replay_model
    set_active_replay_model(str(outp))

    print("OK: saved model ->", outp)
    print("OK: saved mapping ->", mp)
    print("OK: active replay model ->", (Path(settings.MODEL_DIR) / "active_replay_model.json"))

if __name__ == "__main__":
    main()
