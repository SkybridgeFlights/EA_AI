# -*- coding: utf-8 -*-data_fetch.py
import os, sys, argparse, pandas as pd
from pathlib import Path

def load_from_yahoo(symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        print("[data] yfinance غير متاح — تخطٍ")
        return pd.DataFrame()
    tf_map = {"H1":"60m","M30":"30m","M15":"15m","D1":"1d"}
    itv = tf_map.get(tf.upper(), "60m")
    df = yf.download(symbol, interval=itv, start=start or None, end=end or None, auto_adjust=False, progress=False)
    if df.empty: return df
    df = df.reset_index().rename(columns={"Datetime":"Time","Date":"Time"})
    df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
    df = df.dropna(subset=["Time"]).sort_values("Time")
    return df[["Time","Open","High","Low","Close"]]

def load_from_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists(): return pd.DataFrame()
    df = pd.read_csv(p)
    if "Time" not in df.columns: return pd.DataFrame()
    df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
    df = df.dropna(subset=["Time"]).sort_values("Time")
    cols = ["Open","High","Low","Close"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["Time","Open","High","Low","Close"]].dropna()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--csv_fallback", default="data\\XAUUSD_H1.csv")
    ap.add_argument("--out", default="data\\XAUUSD_H1.csv")
    args = ap.parse_args()

    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)

    df = load_from_yahoo(args.symbol, args.tf, args.start, args.end)
    if df.empty:
        print("[data] yahoo فشل/فارغ، استخدام CSV احتياطي:", args.csv_fallback)
        df = load_from_csv(args.csv_fallback)

    if df.empty:
        print("[data] لا توجد بيانات صالحة")
        sys.exit(2)

    df.to_csv(out, index=False)
    print(f"[data] saved -> {out} (rows={len(df)})")

if __name__ == "__main__":
    main()




    