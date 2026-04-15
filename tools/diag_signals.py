# -*- coding: utf-8 -*-diag_signals.py
"""
تشخيص إشارات الدخول (EMA/RSI) مع عدّاد أسباب عدم التنفيذ.
- يدعم CSV / yfinance / MT5
- يطبع ملخص أسباب منع الصفقات + أمثلة أولى
- يمكن حفظ ملف CSV تفصيلي للأسباب (--out_diag_csv)

تشغيل:
python diag_signals.py --source mt5 --mt5_symbol XAUUSDr --mt5_timeframe H1 --mt5_bars 50000
python diag_signals.py --source csv --price data\XAUUSD_H1.csv
python diag_signals.py --source yfinance --symbol XAUUSD
"""
from __future__ import annotations
import argparse, time, math, random, sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd

# ========================= utils =========================
def _ensure_utc_idx(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None: return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")

def _mkdir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def _fmt_pct(x: float) -> str:
    return f"{100.0*x:.1f}%"

# ====================== data loaders =====================
def load_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"[data] CSV غير موجود: {path}")
    return pd.read_csv(path)

def load_from_yf(symbol: str, interval: str="60m", years: int=10) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        raise SystemExit("[data] yfinance غير مثبت. نفّذ: pip install yfinance")
    yf_symbol = "XAUUSD=X" if symbol.upper()=="XAUUSD" else symbol
    df = yf.Ticker(yf_symbol).history(period=f"{years}y", interval=interval)
    if df.empty:
        raise SystemExit(f"[data] yfinance أعاد بيانات فارغة للرمز: {yf_symbol}")
    df = df.rename(columns=str.title)[["Open","High","Low","Close"]].copy()
    df.reset_index(inplace=True)
    if "Datetime" in df.columns: df.rename(columns={"Datetime":"Time"}, inplace=True)
    if "Date" in df.columns: df.rename(columns={"Date":"Time"}, inplace=True)
    return df

def resolve_mt5_symbol(requested: str, verbose=True) -> str:
    import MetaTrader5 as mt5
    if not mt5.initialize(): raise SystemExit("[data] فشل تهيئة MT5 (initialize)")
    try:
        want = (requested or "").upper().strip()
        pats = []
        if want:
            pats += [want, want+"*", want+"r", want+"m", want+".i",
                     want.replace("USD","USDr"), want.replace("USD","USDm")]
        pats += ["XAU*", "GOLD*"]
        found = {}
        for p in pats:
            for s in mt5.symbols_get(p): found[s.name]=True
        if not found: raise SystemExit("[data] لا رموز مطابقة في MT5 (تحقق من Market Watch)")
        names = list(found.keys())
        def score(n):
            N=n.upper()
            return (N.startswith("XAU"), N.startswith(want), -abs(len(N)-(len(want) if want else len(N))), -len(N))
        best = sorted(names, key=score, reverse=True)[0]
        if verbose: print(f"[mt5] resolved: requested='{want}' -> '{best}'")
        return best
    finally:
        mt5.shutdown()

def load_from_mt5(symbol: str, timeframe: str="H1", bars: int=100000, auto=True, verbose=True) -> pd.DataFrame:
    try:
        import MetaTrader5 as mt5
    except Exception:
        raise SystemExit("[data] MetaTrader5 غير مثبت. pip install MetaTrader5")
    sym = resolve_mt5_symbol(symbol, verbose=verbose) if auto else symbol
    tf_map = {"M1":mt5.TIMEFRAME_M1,"M5":mt5.TIMEFRAME_M5,"M15":mt5.TIMEFRAME_M15,
              "M30":mt5.TIMEFRAME_M30,"H1":mt5.TIMEFRAME_H1,"H4":mt5.TIMEFRAME_H4,"D1":mt5.TIMEFRAME_D1}
    if not mt5.initialize(): raise SystemExit("[data] فشل تهيئة MT5")
    try:
        info = mt5.symbol_info(sym); 
        if info is None: raise SystemExit(f"[data] الرمز '{sym}' غير موجود")
        if not info.visible: mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, tf_map.get(timeframe, mt5.TIMEFRAME_H1), 0, int(bars))
    finally:
        mt5.shutdown()
    if rates is None or len(rates)==0: raise SystemExit(f"[data] فارغ من MT5 '{sym}'")
    df = pd.DataFrame(rates)
    df["Time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close"}, inplace=True)
    if verbose: print(f"[data] source=mt5 symbol={sym} tf={timeframe} rows={len(df)}")
    return df[["Time","Open","High","Low","Close"]]

def clean_price_df(df: pd.DataFrame, verbose=False) -> pd.DataFrame:
    if "Time" not in df.columns: raise SystemExit("CSV يجب أن يحتوي الأعمدة: Time,Open,High,Low,Close")
    d = df.copy()
    d["Time"] = pd.to_datetime(d["Time"], utc=True, errors="coerce")
    d = d.dropna(subset=["Time"]).sort_values("Time")
    for c in ["Open","High","Low","Close"]:
        d[c]=pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["Open","High","Low","Close"])
    dup = d["Time"].duplicated(keep="last").sum()
    if dup and verbose: print(f"[data] removed dups: {dup}")
    d = d[~d["Time"].duplicated(keep="last")]
    if verbose:
        print(f"[data] rows= {len(d)} from= {d['Time'].iloc[0]} to= {d['Time'].iloc[-1]}")
        step = d["Time"].diff().value_counts().head(4)
        print(f"[data] time_step_top: {dict(step)}")
        vol = d["Close"].pct_change().rolling(200*24//1, min_periods=200).std().median()
        print(f"[data] median_200d_vol={vol:.6f}")
    return d.reset_index(drop=True)

# ============= feature builder (fallback) =============
def _rsi(c: pd.Series, p: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0.0); dn = (-d.clip(upper=0.0))
    ag = up.ewm(alpha=1.0/p, adjust=False).mean()
    al = dn.ewm(alpha=1.0/p, adjust=False).mean()
    rs = ag/(al+1e-12)
    return 100.0 - (100.0/(1.0+rs))

def make_features(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int) -> pd.DataFrame:
    m = df.copy()
    m["Time"] = pd.to_datetime(m["Time"], utc=True, errors="coerce")
    m = m.dropna(subset=["Time"]).sort_values("Time").set_index("Time")
    close = pd.to_numeric(m["Close"], errors="coerce").astype(float)
    feats = pd.DataFrame(index=_ensure_utc_idx(close.index))
    feats["Close"] = close
    feats["emaf"] = close.ewm(span=int(ema_fast), adjust=False).mean()
    feats["emas"] = close.ewm(span=int(ema_slow), adjust=False).mean()
    feats["rsi"]  = _rsi(close, int(rsi_period))
    feats["atr_pts"] = (close.pct_change().rolling(14, min_periods=5).std().fillna(0.0) * 800.0)
    return feats.ffill().dropna()

# ====================== parameters ======================
@dataclass
class P:
    rsi_buy_max: float = 60
    rsi_sell_min: float = 70  # حد أدنى للبيع (كان sell_max في نسختك)
    ema_fast: int = 10
    ema_slow: int = 26
    rsi_period: int = 14
    min_trade_gap_sec: int = 60*30
    max_trades_per_day: int = 12
    max_spread_pts: int = 600
    base_spread_pts: float = 180.0
    atr_mult: float = 1.6
    rr: float = 2.0
    point: float = 0.01
    commission: float = 7.0

# ===================== diagnostic run ====================
def run_diag(df: pd.DataFrame, par: P, out_diag_csv: Optional[Path]=None, verbose=True) -> Dict:
    f = make_features(df, par.ema_fast, par.ema_slow, par.rsi_period)
    idx = f.index
    close = f["Close"].astype(float)
    emaf, emas, rsi, atr_pts = f["emaf"], f["emas"], f["rsi"], f["atr_pts"]

    # عدادات أسباب المنع
    reasons = {"no_signal":0, "rsi_block":0, "spread_gate":0, "gap_gate":0,
               "daily_gate":0, "ok_entries":0}
    rows: List[Tuple] = []

    last_entry_ts: Optional[pd.Timestamp] = None
    day_count: Dict[str,int] = {}

    for i in range(2, len(idx)):
        ts = pd.Timestamp(idx[i]).tz_convert("UTC")
        mid = float(close.iloc[i])
        # spread ثابت للتشخيص (يمكن تغييره)
        spr_pts = par.base_spread_pts
        # إشارة:
        f_now,f_prev = float(emaf.iloc[i]), float(emaf.iloc[i-1])
        s_now,s_prev = float(emas.iloc[i]), float(emas.iloc[i-1])
        rv = float(rsi.iloc[i])

        cross_up   = (f_prev <= s_prev and f_now > s_now)
        cross_down = (f_prev >= s_prev and f_now < s_now)
        near_eps   = 2e-4
        near_up    = (f_now >= s_now*(1-near_eps) and f_now > s_now)
        near_down  = (f_now <= s_now*(1+near_eps) and f_now < s_now)

        buy_ok  = (cross_up or near_up) and (rv <= par.rsi_buy_max)
        sell_ok = (cross_down or near_down) and (rv >= par.rsi_sell_min)

        if not (buy_ok or sell_ok):
            reasons["no_signal"] += 1
            if out_diag_csv is not None:
                rows.append((ts.isoformat(),"no_signal", rv, f_now-s_now, spr_pts))
            continue

        # قيود
        if spr_pts > par.max_spread_pts:
            reasons["spread_gate"] += 1
            if out_diag_csv is not None:
                rows.append((ts.isoformat(),"spread_gate", rv, f_now-s_now, spr_pts))
            continue

        if last_entry_ts is not None and (ts - last_entry_ts).total_seconds() < par.min_trade_gap_sec:
            reasons["gap_gate"] += 1
            if out_diag_csv is not None:
                rows.append((ts.isoformat(),"gap_gate", rv, f_now-s_now, spr_pts))
            continue

        k = ts.date().isoformat()
        cnt = day_count.get(k,0)+1
        if cnt > par.max_trades_per_day:
            reasons["daily_gate"] += 1
            if out_diag_csv is not None:
                rows.append((ts.isoformat(),"daily_gate", rv, f_now-s_now, spr_pts))
            continue
        day_count[k]=cnt

        reasons["ok_entries"] += 1
        last_entry_ts = ts
        if out_diag_csv is not None:
            rows.append((ts.isoformat(),"ok", rv, f_now-s_now, spr_pts))

    total_bars = len(idx)
    if verbose:
        print("\n========= DIAG SUMMARY =========")
        def pct(x): return f"{x} ({_fmt_pct(x/max(1,total_bars))})"
        for k in ["ok_entries","no_signal","rsi_block","spread_gate","gap_gate","daily_gate"]:
            if k not in reasons: reasons[k]=0
        print(f"bars: {total_bars}")
        print(f"ok_entries : {pct(reasons['ok_entries'])}")
        print(f"no_signal  : {pct(reasons['no_signal'])}   ← لا يوجد تقاطع/RSI مناسب")
        print(f"spread_gate: {pct(reasons['spread_gate'])}   ← السبريد تجاوز الحد")
        print(f"gap_gate   : {pct(reasons['gap_gate'])}   ← حد الفاصل الزمني بين الصفقات")
        print(f"daily_gate : {pct(reasons['daily_gate'])}   ← تجاوز حد صفقات اليوم")
        print("================================\n")

        # أمثلة أول 10 سطور من الحالات المسجلة
        if out_diag_csv is None:
            print("[hint] استخدم --out_diag_csv لحفظ جميع الحالات في ملف CSV للمراجعة في Excel/CSV.\n")

    if out_diag_csv is not None:
        _mkdir(out_diag_csv)
        pd.DataFrame(rows, columns=["time","reason","rsi","emaf_minus_emas","spread_pts"]).to_csv(out_diag_csv, index=False, encoding="utf-8")
        print(f"[diag] saved -> {out_diag_csv}")

    return reasons

# ======================= CLI main =======================
def main():
    ap = argparse.ArgumentParser(description="Signal Diagnosis (EMA/RSI)")
    ap.add_argument("--source", default="csv", choices=["csv","yfinance","mt5"])
    ap.add_argument("--price", default="data\\XAUUSD_H1.csv")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--mt5_symbol", default="")
    ap.add_argument("--mt5_timeframe", default="H1")
    ap.add_argument("--mt5_bars", type=int, default=80000)
    ap.add_argument("--years", type=int, default=10)   # yfinance
    ap.add_argument("--interval", default="60m")       # yfinance
    # معلمات التشخيص القابلة للتعديل سريعًا
    ap.add_argument("--ema_fast", type=int, default=10)
    ap.add_argument("--ema_slow", type=int, default=26)
    ap.add_argument("--rsi_period", type=int, default=14)
    ap.add_argument("--rsi_buy_max", type=float, default=62.0)
    ap.add_argument("--rsi_sell_min", type=float, default=70.0)
    ap.add_argument("--min_gap_min", type=int, default=30)
    ap.add_argument("--max_trades_day", type=int, default=12)
    ap.add_argument("--max_spread_pts", type=float, default=600.0)
    ap.add_argument("--base_spread_pts", type=float, default=180.0)
    ap.add_argument("--out_diag_csv", default="")
    args = ap.parse_args()

    # تحميل البيانات
    if args.source == "csv":
        df_raw = load_from_csv(Path(args.price))
    elif args.source == "yfinance":
        df_raw = load_from_yf(args.symbol, interval=args.interval, years=args.years)
    else:
        df_raw = load_from_mt5(args.mt5_symbol or args.symbol, timeframe=args.mt5_timeframe, bars=args.mt5_bars, auto=True, verbose=True)

    df = clean_price_df(df_raw, verbose=True)

    par = P(
        rsi_buy_max=args.rsi_buy_max,
        rsi_sell_min=args.rsi_sell_min,
        ema_fast=args.ema_fast,
        ema_slow=args.ema_slow,
        rsi_period=args.rsi_period,
        min_trade_gap_sec=int(args.min_gap_min*60),
        max_trades_per_day=int(args.max_trades_day),
        max_spread_pts=float(args.max_spread_pts),
        base_spread_pts=float(args.base_spread_pts),
    )

    out_csv = Path(args.out_diag_csv) if args.out_diag_csv else None
    run_diag(df, par, out_diag_csv=out_csv, verbose=True)

if __name__ == "__main__":
    main()







