# tools/generate_ai_replay_csv_xgb.py
import argparse
import json
import sys
from pathlib import Path
from typing import Optional, List

import pandas as pd
import numpy as np

# ---------------------------
# Ensure project root on sys.path (fix: No module named 'app')
# ---------------------------
ROOT = Path(__file__).resolve().parents[1]  # .../EA_AI
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.ml.features import make_features
from app.ml.model import _align_features
from app.ml.replay_registry import load_active_replay_model


# ---------------------------
# IO helpers
# ---------------------------
def read_mt5_prices_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    df = pd.read_csv(p, sep=None, engine="python")
    df.columns = [c.strip() for c in df.columns]

    if "time" in df.columns:
        dt = pd.to_datetime(df["time"], errors="coerce", utc=True)
        df = df.drop(columns=["time"])
        df.insert(0, "dt", dt)
    elif "Time" in df.columns:
        dt = pd.to_datetime(df["Time"], errors="coerce", utc=True)
        df = df.drop(columns=["Time"])
        df.insert(0, "dt", dt)
    else:
        dt = pd.to_datetime(df.iloc[:, 0], errors="coerce", utc=True)
        df = df.drop(columns=[df.columns[0]])
        df.insert(0, "dt", dt)

    df = df.dropna(subset=["dt"]).copy()
    df = df.sort_values("dt").drop_duplicates("dt", keep="last")
    df = df.set_index("dt")

    rename_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open":
            rename_map[c] = "Open"
        elif cl == "high":
            rename_map[c] = "High"
        elif cl == "low":
            rename_map[c] = "Low"
        elif cl == "close":
            rename_map[c] = "Close"
        elif cl in ("volume", "tick_volume", "tickvol", "tickvol."):
            rename_map[c] = "Volume"
    df = df.rename(columns=rename_map)

    need = ["Open", "High", "Low", "Close"]
    for c in need:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}. Found: {list(df.columns)}")

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df.astype(float)


def _symbol_fallbacks(symbol: str) -> List[str]:
    s = symbol.strip()
    out = [s]
    # شائع في MT5: XAUUSD vs XAUUSDr
    if s.endswith("r") and len(s) > 1:
        out.append(s[:-1])
    return list(dict.fromkeys(out))


def load_mapping(symbol: str) -> dict:
    for sym in _symbol_fallbacks(symbol):
        p = Path(settings.MODEL_DIR) / f"mapping_{sym}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return {int(k): int(v) for k, v in d.items()}
    # fallback safe (لكن الأفضل ألا نصل هنا)
    return {0: -1, 1: 0, 2: 1}


# ---------------------------
# Auto-discovery of price files
# ---------------------------
def _default_search_dirs() -> List[Path]:
    # عدّل/أضف مجلداتك هنا حسب مشروعك
    return [
        ROOT / "data",
        ROOT / "mt5_exports",
        ROOT / "prices",
        ROOT / "datasets",
        ROOT,  # كحل أخير
    ]


def auto_find_prices(symbol: str, tf: str, search_dirs: Optional[List[Path]] = None) -> Path:
    tf = tf.strip().upper()
    if search_dirs is None:
        search_dirs = _default_search_dirs()

    patterns = []
    for sym in _symbol_fallbacks(symbol):
        # أمثلة أسماء شائعة: prices_XAUUSDr_H1_2020-2024.csv
        patterns.append(f"*{sym}*_{tf}_*.csv")
        patterns.append(f"*{sym}*{tf}*.csv")

    candidates: List[Path] = []
    for d in search_dirs:
        if not d.exists():
            continue
        for pat in patterns:
            candidates.extend(list(d.rglob(pat)))

    # فلترة: فقط ملفات CSV
    candidates = [p for p in candidates if p.is_file() and p.suffix.lower() == ".csv"]

    if not candidates:
        searched = "\n  - ".join(str(x) for x in search_dirs)
        raise FileNotFoundError(
            f"Could not auto-find prices CSV for symbol={symbol}, tf={tf}.\n"
            f"Searched dirs:\n  - {searched}\n"
            f"Expected something like: prices_{symbol}_{tf}_2020-2024.csv"
        )

    # اختَر الأحدث تعديلًا (عادة آخر ملف تم تصديره)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def infer_range_from_prices(df: pd.DataFrame) -> str:
    if df.empty:
        return "unknown"
    a = df.index.min()
    b = df.index.max()
    try:
        return f"{a:%Y%m%d}-{b:%Y%m%d}"
    except Exception:
        return "unknown"


# ---------------------------
# Optional resampling (H1 -> H4) if needed
# ---------------------------
def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # يعتمد على كون index = dt (UTC)
    o = df["Open"].resample(rule).first()
    h = df["High"].resample(rule).max()
    l = df["Low"].resample(rule).min()
    c = df["Close"].resample(rule).last()
    v = df["Volume"].resample(rule).sum()
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["Open", "High", "Low", "Close", "Volume"]
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out


# ---------------------------
# Core logic
# ---------------------------
def build_signals(
    df_price: pd.DataFrame,
    symbol: str,
    min_conf: float,
    flat_filter: bool,
    max_flat: float,
    margin_filter: bool,
    min_margin: float,
) -> pd.DataFrame:
    model = load_active_replay_model()
    if model is None:
        raise RuntimeError(
            "No active REPLAY model. Train it first:\n"
            "python tools/train_replay_model_xgb.py --prices ... --symbol XAUUSDr"
        )

    df_news = pd.DataFrame(columns=["time", "impact", "currency"])
    X = make_features(df_price, df_news)
    if X is None or X.empty:
        raise RuntimeError("No features produced.")

    X = _align_features(model, X)
    probs = model.predict_proba(X)

    mapping = load_mapping(symbol)            # {class_index -> -1/0/+1}
    rev = {v: k for k, v in mapping.items()}  # {-1/0/+1 -> class_index}

    idx_neg = rev.get(-1, 0)
    idx_flat = rev.get(0, 1)
    idx_pos = rev.get(1, 2)

    p_sell = probs[:, idx_neg] if idx_neg < probs.shape[1] else np.zeros(len(X))
    p_flat = probs[:, idx_flat] if idx_flat < probs.shape[1] else np.zeros(len(X))
    p_buy = probs[:, idx_pos] if idx_pos < probs.shape[1] else np.zeros(len(X))

    dir_is_buy = p_buy > p_sell
    p_dir = np.maximum(p_buy, p_sell)
    margin = np.abs(p_buy - p_sell)

    take = (p_dir >= float(min_conf))
    if flat_filter:
        take = take & (p_flat <= float(max_flat))
    if margin_filter:
        take = take & (margin >= float(min_margin))

    direction = np.where(dir_is_buy, 1, -1)
    direction = np.where(take, direction, 0)

    out = pd.DataFrame(index=X.index)
    out["dir"] = direction.astype(int)
    out["conf"] = np.where(direction == 0, 0.0, p_dir).astype(float)
    out["p_buy"] = p_buy.astype(float)
    out["p_sell"] = p_sell.astype(float)
    out["p_flat"] = p_flat.astype(float)
    out["margin"] = margin.astype(float)
    out = out.reset_index().rename(columns={"index": "time"})
    return out


def main():
    ap = argparse.ArgumentParser()

    # Either provide --prices manually OR use --auto_prices
    ap.add_argument("--prices", default=None, help="MT5 exported prices CSV path (optional if --auto_prices)")
    ap.add_argument("--auto_prices", action="store_true", help="Auto-find prices file based on --symbol and --tf")

    ap.add_argument("--out", default=None, help="Output signals CSV path (optional; auto if not set)")
    ap.add_argument("--symbol", default=getattr(settings, "SYMBOL", "XAUUSDr"))
    ap.add_argument("--tf", default="H1", help="Timeframe label used for auto-find & naming (H1/H4)")

    ap.add_argument("--min_conf", type=float, default=0.65)
    ap.add_argument("--flat_filter", action="store_true")
    ap.add_argument("--max_flat", type=float, default=0.55)
    ap.add_argument("--margin_filter", action="store_true")
    ap.add_argument("--min_margin", type=float, default=0.06)

    # Optional: resample (useful if you only have H1 and want H4)
    ap.add_argument("--resample_to", default=None, help="Resample OHLCV to rule (e.g. 4H). Use with care.")

    args = ap.parse_args()

    # Determine prices path
    if args.auto_prices:
        prices_path = auto_find_prices(args.symbol, args.tf)
    else:
        if not args.prices:
            raise SystemExit("Provide --prices or use --auto_prices")
        prices_path = Path(args.prices)

    dfp = read_mt5_prices_csv(str(prices_path))

    if args.resample_to:
        dfp = resample_ohlcv(dfp, args.resample_to)

    sig = build_signals(
        dfp, args.symbol, args.min_conf,
        args.flat_filter, args.max_flat,
        args.margin_filter, args.min_margin,
    )

    # Determine output path
    if args.out:
        outp = Path(args.out)
    else:
        rng = infer_range_from_prices(dfp)
        outp = ROOT / "ai_signals" / f"signals_{args.symbol}_{args.tf}_{rng}.csv"

    outp.parent.mkdir(parents=True, exist_ok=True)
    sig.to_csv(outp, index=False, encoding="utf-8")

    print(f"OK: prices={prices_path}")
    print(f"OK: wrote {len(sig)} rows -> {outp}")
    print("columns:", list(sig.columns))


if __name__ == "__main__":
    main()
