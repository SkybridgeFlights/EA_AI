# tools/generate_ai_replay_csv_xgb.py
# Updated: supports --resample_to H4 / H1 / M15 (auto-normalized to pandas freq like 4H / 1H / 15T)
# Also supports --auto_prices with --root and --prefer_years to find prices_*.csv automatically.
# Robust CSV reading: header/no-header, delimiter detection, encoding fallbacks (utf-16, utf-8-sig, cp1252).

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import settings
from app.ml.features import make_features
from app.ml.model import _align_features
from app.ml.replay_registry import load_active_replay_model


# ----------------------------
# Helpers: encoding + CSV read
# ----------------------------
def _read_csv_flexible(path: Path) -> pd.DataFrame:
    # Try multiple encodings (MT5 exports often utf-16 with BOM)
    encodings = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be", "cp1252", "latin1"]
    last_err = None
    for enc in encodings:
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding=enc)
        except Exception as e:
            last_err = e
            continue
    raise last_err


def _parse_time_series(col: pd.Series) -> pd.Series:
    # Handles formats like:
    # 2014.01.14 00:00
    # 2014-01-14 00:00:00
    # 2014.01.14 00:00:00
    s = col.astype(str).str.strip()
    s = s.str.replace(r"\s+", " ", regex=True)
    # Convert MT5 dot-date to dash-date for better parsing
    s = s.str.replace(r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", regex=True)
    dt = pd.to_datetime(s, errors="coerce", utc=True)
    return dt


def read_mt5_prices_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    df = _read_csv_flexible(p)
    df.columns = [str(c).strip() for c in df.columns]

    # If file has NO header, pandas may auto-use first row as header (numbers)
    # Detect by checking if required fields appear in columns; otherwise re-read as header=None.
    lower_cols = [c.lower() for c in df.columns]
    has_any_ohlc = any(x in lower_cols for x in ["open", "high", "low", "close"])
    has_time_col = any(x in lower_cols for x in ["time", "date", "datetime"])
    if (not has_any_ohlc) and (not has_time_col):
        df = None
        # Re-read as header=None with delimiter detection (comma/tab/semicolon)
        # First try comma, then tab, then semicolon.
        for sep in [",", "\t", ";"]:
            for enc in ["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin1"]:
                try:
                    tmp = pd.read_csv(p, header=None, sep=sep, encoding=enc, engine="python")
                    if tmp.shape[1] >= 5:
                        df = tmp
                        break
                except Exception:
                    continue
            if df is not None:
                break
        if df is None:
            raise ValueError(f"Could not parse CSV (no header) from: {p}")

        # Keep first 6 columns: time, Open, High, Low, Close, Volume
        # MT5 sometimes has 7 columns (extra "spread" or "real volume"); we ignore extras.
        df = df.iloc[:, :6].copy()
        df.columns = ["time", "Open", "High", "Low", "Close", "Volume"]
    else:
        # Normalize typical MT5 header variants
        rename_map = {}
        for c in df.columns:
            cl = c.lower()
            if cl in ("time", "date", "datetime"):
                rename_map[c] = "time"
            elif cl == "open":
                rename_map[c] = "Open"
            elif cl == "high":
                rename_map[c] = "High"
            elif cl == "low":
                rename_map[c] = "Low"
            elif cl == "close":
                rename_map[c] = "Close"
            elif cl in ("volume", "tick_volume", "tickvol", "tickvol.", "tick volume"):
                rename_map[c] = "Volume"
        df = df.rename(columns=rename_map)

        # If time not present, assume first column is time
        if "time" not in df.columns:
            df = df.copy()
            df.insert(0, "time", df.iloc[:, 0])

    # Parse time
    dt = _parse_time_series(df["time"])
    df = df.drop(columns=["time"])
    df.insert(0, "dt", dt)

    df = df.dropna(subset=["dt"]).copy()
    df = df.sort_values("dt").drop_duplicates("dt", keep="last")
    df = df.set_index("dt")

    need = ["Open", "High", "Low", "Close"]
    for c in need:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}. Found: {list(df.columns)}")

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df.astype(float)


# ----------------------------
# Resampling (H4 / 4H etc.)
# ----------------------------
_TF_RE = re.compile(r"^(?:(\d+)\s*)?([a-zA-Z]+)\s*(\d+)?$")


def normalize_tf_to_pandas_freq(tf: str) -> str:
    """
    Accepts: H4, 4H, H1, 1H, M15, 15M, M5, 5M, D1, 1D, W1, 1W
    Returns pandas freq: 4H, 1H, 15T, 5T, 1D, 1W
    """
    s = str(tf).strip().upper().replace(" ", "")
    if not s:
        raise ValueError("Empty timeframe")

    # If already pandas-like e.g. 4H, 15T, 30MIN, 1D, 1W
    # Convert common variants:
    s = s.replace("MIN", "T")  # 15MIN -> 15T

    # Try patterns:
    # H4  -> unit=H, n=4
    # 4H  -> n=4, unit=H
    # M15 -> unit=M, n=15
    # 15M -> n=15, unit=M
    m = _TF_RE.match(s)
    if not m:
        raise ValueError(f"Invalid timeframe: {tf}")

    a_num, unit, b_num = m.group(1), m.group(2), m.group(3)

    # Determine n and unit
    if a_num is not None and b_num is not None:
        # Something weird like 4H1
        raise ValueError(f"Invalid timeframe: {tf}")

    if a_num is not None:
        n = int(a_num)
        u = unit
    elif b_num is not None:
        n = int(b_num)
        u = unit
    else:
        # e.g. "H" -> 1H
        n = 1
        u = unit

    # Normalize units
    # MT-style: M=minutes, H=hours, D=days, W=weeks
    if u in ("M", "MIN", "T"):
        return f"{n}T"  # pandas uses T for minutes
    if u in ("H",):
        return f"{n}H"
    if u in ("D",):
        return f"{n}D"
    if u in ("W",):
        return f"{n}W"

    raise ValueError(f"Unsupported timeframe unit: {u} (from {tf})")


def resample_ohlcv(df: pd.DataFrame, to_tf: str) -> pd.DataFrame:
    rule = normalize_tf_to_pandas_freq(to_tf)

    # OHLCV aggregation
    o = df["Open"].resample(rule).first()
    h = df["High"].resample(rule).max()
    l = df["Low"].resample(rule).min()
    c = df["Close"].resample(rule).last()
    v = df["Volume"].resample(rule).sum()

    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["Open", "High", "Low", "Close", "Volume"]
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out


# ----------------------------
# Model + mapping
# ----------------------------
def _symbol_fallbacks(symbol: str) -> list[str]:
    s = symbol.strip()
    out = [s]
    if s.endswith("r") and len(s) > 1:
        out.append(s[:-1])
    return list(dict.fromkeys(out))


def load_mapping(symbol: str) -> dict:
    for sym in _symbol_fallbacks(symbol):
        p = Path(settings.MODEL_DIR) / f"mapping_{sym}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return {int(k): int(v) for k, v in d.items()}
    return {0: -1, 1: 0, 2: 1}


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
    out = out.reset_index().rename(columns={"index": "dt"})
    return out


# ----------------------------
# Auto-find prices
# ----------------------------
def find_prices_file(root: Path, symbol: str, tf: str, prefer_years: str | None) -> Path | None:
    tf_u = str(tf).upper().replace(" ", "")
    sym = symbol.strip()

    patterns = [
        f"prices_{sym}_{tf_u}_*.csv",
        f"prices_{sym}{tf_u}*.csv",  # some exports
        f"*prices*{sym}*{tf_u}*.csv",
    ]

    candidates: list[Path] = []
    for pat in patterns:
        candidates.extend(root.rglob(pat))

    # Also search Desktop as common place (optional but helpful)
    try:
        desktop = Path.home() / "OneDrive" / "Desktop"
        if desktop.exists():
            for pat in patterns:
                candidates.extend(desktop.rglob(pat))
    except Exception:
        pass

    # Filter by prefer_years if provided (e.g. "2020-2024")
    if prefer_years:
        candidates_yr = [c for c in candidates if prefer_years in c.name]
        if candidates_yr:
            candidates = candidates_yr

    # Pick newest
    candidates = [c for c in candidates if c.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--prices", help="MT5 exported prices CSV path")
    ap.add_argument("--auto_prices", action="store_true", help="Auto-find prices CSV under --root")
    ap.add_argument("--root", default=str(Path.cwd()), help="Root folder to search in when using --auto_prices")
    ap.add_argument("--prefer_years", default=None, help='Filter filename by substring e.g. "2020-2024"')

    ap.add_argument("--out", required=True, help="Output signals CSV path")
    ap.add_argument("--symbol", default=getattr(settings, "SYMBOL", "XAUUSDr"))
    ap.add_argument("--tf", default="H1", help="TF label used for auto search only (e.g. H1, H4, M15)")
    ap.add_argument("--resample_to", default=None, help="Resample OHLCV to another TF (e.g. H4 or 4H)")

    ap.add_argument("--min_conf", type=float, default=0.65)
    ap.add_argument("--flat_filter", action="store_true")
    ap.add_argument("--max_flat", type=float, default=0.55)
    ap.add_argument("--margin_filter", action="store_true")
    ap.add_argument("--min_margin", type=float, default=0.06)

    args = ap.parse_args()

    prices_path: Path | None = None
    if args.auto_prices:
        root = Path(args.root).expanduser().resolve()
        prices_path = find_prices_file(root, args.symbol, args.tf, args.prefer_years)
        if prices_path is None:
            raise FileNotFoundError(
                f"Could not auto-find prices file under: {root}\n"
                f"Tried symbol={args.symbol} tf={args.tf} prefer_years={args.prefer_years}"
            )
    else:
        if not args.prices:
            raise ValueError("Either provide --prices or use --auto_prices")
        prices_path = Path(args.prices).expanduser().resolve()

    dfp = read_mt5_prices_csv(str(prices_path))

    if args.resample_to:
        dfp = resample_ohlcv(dfp, args.resample_to)

    sig = build_signals(
        dfp,
        args.symbol,
        args.min_conf,
        args.flat_filter,
        args.max_flat,
        args.margin_filter,
        args.min_margin,
    )

    outp = Path(args.out).expanduser().resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)
    sig.to_csv(outp, index=False, encoding="utf-8")

    print(f"OK: prices={prices_path}")
    if args.resample_to:
        print(f"OK: resample_to={args.resample_to} (pandas_freq={normalize_tf_to_pandas_freq(args.resample_to)})")
    print(f"OK: wrote {len(sig)} rows -> {outp}")
    print("columns:", list(sig.columns))


if __name__ == "__main__":
    main()