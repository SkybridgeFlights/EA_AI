# -*- coding: utf-8 -*-optimize_auto.py
r"""
تحسين تلقائي Walk-Forward + Bayesian/Random مع إدارة شُحّ الإشارات:
- تحميل بيانات (CSV / yfinance / MT5) + تنظيف + UTC
- Backtest يحاكي EMA/RSI + ATR SL/TP + BE/TS + تكاليف
- هدف موحّد PF/WinRate/Trades/DD + حماية overfit
- بحث موازي + توسّع تلقائي مع قيود وقت وصبر
- ترقية Shadow→Live عبر live_config.json ثم تحويله إلى INI لاحقًا بواسطة SelfCal

تشغيل مثال:
python tools\optimize_auto.py --source mt5 --mt5_symbol XAUUSDr --mt5_timeframe H1 --mt5_bars 60000 --outdir artifacts --windows 3 --min_trades 12 --use_bayes 1 --tries 800 --expand 1 --max_expand 8 --timeout_min 20 --patience 3 --objective pf,wr,trades,dd --w_pf 0.7 --w_wr 0.2 --w_trades 2.0 --w_dd 0.00005 --seed 42 --jobs -1 --verbose 1 --daemon 0 --loops 1
"""
from __future__ import annotations

import os, sys, json, math, argparse, time, random, signal, warnings
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
import multiprocessing as mp

import numpy as np
import pandas as pd
np.seterr(all="ignore")
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ===== ميزات من مشروعك (نسخة احتياطية عند اللزوم) =====
try:
    from app.ml.features import make_features
except Exception:
    def _rsi(c: pd.Series, p: int) -> pd.Series:
        d = c.diff()
        up = d.clip(lower=0.0)
        dn = (-d.clip(upper=0.0))
        ag = up.ewm(alpha=1.0 / p, adjust=False).mean()
        al = dn.ewm(alpha=1.0 / p, adjust=False).mean()
        rs = ag / (al + 1e-12)
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(h: pd.Series, l: pd.Series, c: pd.Series, p: int) -> pd.Series:
        pc = c.shift(1)
        tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / p, adjust=False).mean()

    def make_features(df_price: pd.DataFrame, df_news: pd.DataFrame) -> pd.DataFrame:
        df = df_price.copy()
        if "Time" in df.columns:
            df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
            df = df.dropna(subset=["Time"]).sort_values("Time").set_index("Time")
        else:
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
        close = pd.to_numeric(df["Close"], errors="coerce")
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        rsi = _rsi(close, 14)
        atr = _atr(pd.to_numeric(df["High"], errors="coerce"),
                   pd.to_numeric(df["Low"], errors="coerce"),
                   close, 14)
        out = pd.DataFrame(
            {"Close": close, "ema20": ema20, "ema50": ema50, "rsi14": rsi, "atr14": atr},
            index=df.index,
        )
        return out.ffill().fillna(0.0)

# ========= أدوات عامة =========
def _ensure_utc_idx(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")

def _now_iso() -> str:
    return pd.Timestamp.utcnow().isoformat()

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)

def _mkdir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

# ========= تنظيف البيانات =========
def clean_price_df(df: pd.DataFrame, verbose: bool=False) -> Tuple[pd.DataFrame, Dict]:
    stats = {"rows_in": len(df), "dup_removed": 0, "nan_rows": 0, "spike_rows": 0}
    if "Time" not in df.columns:
        raise SystemExit("CSV يجب أن يحتوي الأعمدة: Time,Open,High,Low,Close")
    df = df.copy()
    df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
    nan_before = df["Time"].isna().sum()
    df = df.dropna(subset=["Time"])
    stats["nan_rows"] = int(nan_before)
    df = df.sort_values("Time")
    before = len(df)
    df = df[~df["Time"].duplicated(keep="last")]
    stats["dup_removed"] = int(before - len(df))
    for c in ["Open","High","Low","Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open","High","Low","Close"])
    df["Close_shift"] = df["Close"].shift(1)
    ret = (df["Close"] - df["Close_shift"]).abs() / (df["Close_shift"].abs()+1e-12)
    spikes = ret > 0.20
    stats["spike_rows"] = int(spikes.sum())
    df = df[~spikes].drop(columns=["Close_shift"])
    return df.reset_index(drop=True), stats

# ========= مصادر البيانات =========
def load_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return pd.read_csv(path)

def load_from_yf(symbol: str, interval: str="60m", years: int=10) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        raise SystemExit("[data] yfinance غير مثبت. pip install yfinance")
    yf_symbol = "XAUUSD=X" if symbol.upper()=="XAUUSD" else symbol
    tkr = yf.Ticker(yf_symbol)
    per = f"{years}y"
    df = tkr.history(period=per, interval=interval)
    if df.empty:
        raise SystemExit(f"[data] yfinance أعاد بيانات فارغة: {yf_symbol}")
    df = df.rename(columns=str.title)
    df = df[["Open","High","Low","Close"]].copy()
    df.reset_index(inplace=True)
    if "Datetime" in df.columns:
        df.rename(columns={"Datetime":"Time"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date":"Time"}, inplace=True)
    return df

def resolve_mt5_symbol(requested: str, verbose: bool=True) -> str:
    try:
        import MetaTrader5 as mt5
    except Exception:
        raise SystemExit("[data] MetaTrader5 غير مثبت. pip install MetaTrader5")
    if not mt5.initialize():
        raise SystemExit("[data] فشل initialize()")
    try:
        want = (requested or "").strip()
        patterns = []
        if want:
            base = want.upper()
            patterns.extend([base, base + "*", base + "r", base + "m", base + ".i",
                             base.replace("USD","USDr"), base.replace("USD","USDm")])
        patterns.extend(["XAU*", "GOLD*"])
        found = {}
        for pat in patterns:
            for s in mt5.symbols_get(pat):
                found[s.name] = True
        if not found:
            raise SystemExit("[data] لا رموز متطابقة على MT5")
        names = list(found.keys())
        want_up = (want or "").upper()
        def score(name: str) -> tuple:
            n = name.upper()
            return (n.startswith("XAU"),
                    n.startswith(want_up) if want_up else False,
                    -abs(len(n) - (len(want_up) if want_up else len(n))),
                    -len(n))
        best = sorted(names, key=lambda x: score(x), reverse=True)[0]
        if verbose:
            print(f"[mt5] resolved: '{want}' -> '{best}'")
        return best
    finally:
        mt5.shutdown()

def load_from_mt5(symbol: str, timeframe: str="H1", bars: int=100000, auto: bool=True, verbose: bool=True) -> pd.DataFrame:
    try:
        import MetaTrader5 as mt5
    except Exception:
        raise SystemExit("[data] MetaTrader5 غير مثبت")
    sym = resolve_mt5_symbol(symbol, verbose=verbose) if auto else (symbol or "").strip()
    if not sym:
        raise SystemExit("[data] رمز MT5 غير صالح")
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1
    }
    if not mt5.initialize():
        raise SystemExit("[data] فشل initialize()")
    try:
        info = mt5.symbol_info(sym)
        if info is None:
            raise SystemExit(f"[data] الرمز '{sym}' غير موجود")
        if not info.visible:
            mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, tf_map.get(timeframe, mt5.TIMEFRAME_H1), 0, int(bars))
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise SystemExit(f"[data] بيانات فارغة للرمز '{sym}'")
    df = pd.DataFrame(rates)
    df["Time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close"}, inplace=True)
    if verbose:
        print(f"[data] source=mt5 symbol={sym} tf={timeframe} -> rows={len(df)}")
    return df[["Time","Open","High","Low","Close"]]

def load_or_fetch_price(path: Path, symbol: str, source: str, fetch_if_missing: bool,
                        mt5_symbol: str, mt5_timeframe: str, mt5_bars: int,
                        min_fetch_gap_min: int, out_cache: Path, verbose: bool) -> pd.DataFrame:
    s = (source or "csv").lower()
    if s == "mt5":
        if verbose: print("[data] forcing fresh from MT5")
        df_raw = load_from_mt5(mt5_symbol or symbol, timeframe=mt5_timeframe, bars=mt5_bars, auto=True, verbose=verbose)
        df_clean, stats = clean_price_df(df_raw, verbose=verbose)
        _mkdir(out_cache); df_clean.to_csv(out_cache, index=False)
        if verbose: print(f"[data] source=mt5 -> rows={len(df_clean)} | stats={stats}")
        return df_clean
    if out_cache.exists():
        mtime = out_cache.stat().st_mtime
        age_min = (time.time() - mtime) / 60.0
        if age_min < min_fetch_gap_min:
            if verbose: print(f"[data] using cache -> {out_cache} (age {age_min:.1f} min)")
            return pd.read_csv(out_cache)
    order = ["csv","yfinance","mt5"] if s=="csv" else (["yfinance","csv","mt5"] if s=="yfinance" else ["csv","yfinance","mt5"])
    last_err = None
    for src in order:
        try:
            if src=="csv":
                if not path.exists():
                    if not fetch_if_missing:
                        raise FileNotFoundError(str(path))
                    raise FileNotFoundError(str(path))
                df_raw = load_from_csv(path)
            elif src=="yfinance":
                df_raw = load_from_yf(symbol)
            else:
                df_raw = load_from_mt5(mt5_symbol or symbol, timeframe=mt5_timeframe, bars=mt5_bars, auto=True, verbose=verbose)
            df_clean, stats = clean_price_df(df_raw, verbose=verbose)
            _mkdir(out_cache); df_clean.to_csv(out_cache, index=False)
            if verbose: print(f"[data] source={src} -> rows={len(df_clean)} | stats={stats}")
            return df_clean
        except Exception as e:
            last_err = e
            if verbose: print(f"[data] {src} failed: {e}")
            continue
    raise SystemExit(f"[data] فشل تحميل البيانات من كل المصادر. آخر خطأ: {last_err}")

# ========= نماذج تكلفة =========
def load_spread_by_hour(csv_path: Optional[str]) -> Optional[pd.Series]:
    if not csv_path: return None
    p = Path(csv_path)
    if not p.exists(): return None
    df = pd.read_csv(p)
    if "hour" not in df.columns or "pts" not in df.columns:
        return None
    s = pd.Series(df["pts"].values, index=df["hour"].astype(int))
    return s

def spread_for_hour(hour: int, base_pts: float, table: Optional[pd.Series]) -> float:
    if table is None: return float(base_pts)
    return float(table.get(int(hour), base_pts))

# ========= رينجات ديناميكية =========
def data_driven_ranges(df: pd.DataFrame) -> Dict[str, tuple]:
    m = df.copy()
    m["Time"] = pd.to_datetime(m["Time"], utc=True, errors="coerce")
    m = m.dropna(subset=["Time"]).sort_values("Time").set_index("Time")
    close = pd.to_numeric(m["Close"], errors="coerce").ffill()
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().add(1e-9)
    rsi = 100 - 100/(1 + up/dn)
    rsi = rsi.dropna()
    if len(rsi) < 100:
        return {"rsi_buy_max": (55, 70), "rsi_sell_max": (80, 90), "ema_fast": (5, 12), "ema_slow": (20, 50)}
    p40 = int(np.percentile(rsi, 40))
    p70 = int(np.percentile(rsi, 70))
    p80 = int(np.percentile(rsi, 80))
    p85 = int(np.percentile(rsi, 85))
    vol = close.pct_change().rolling(200, min_periods=50).std().median()
    fast_max = 14 if (vol is not None and vol < 0.002) else 10
    slow_min = 18 if (vol is not None and vol < 0.002) else 12
    return {
        "rsi_buy_max": (p40, max(p70, 55)),
        "rsi_sell_max": (max(p80, 75), min(p85+5, 92)),
        "ema_fast": (3, int(fast_max)),
        "ema_slow": (max(10, slow_min), 60),
    }

# ========= Backtester =========
@dataclass
class P:
    rsi_buy_max: float = 60
    rsi_sell_max: float = 80
    atr_mult: float = 1.8
    rr: float = 2.0
    ts_start: int = 160
    ts_step: int = 50
    be_trig: int = 50
    be_offs: int = 10
    risk_pct: float = 0.6
    spread_pts: float = 150
    commission: float = 7.0
    point: float = 0.01
    digits: int = 2
    min_trade_gap_sec: int = 1200
    max_trades_per_day: int = 20
    max_spread_pts: int = 900
    slippage_pts: float = 0.0
    ema_fast: int = 12
    ema_slow: int = 26
    rsi_period: int = 14
    cross_confirm: int = 0
    max_hold_bars: int = 240
    flip_on_opposite: int = 1

def run_bt(df: pd.DataFrame, par: P,
           spread_hourly: Optional[pd.Series]=None,
           verbose: bool=False) -> Dict:
    feats = make_features(df, pd.DataFrame())
    if "Close" not in feats.columns:
        return {"trades": 0, "pf": 0.0, "winrate": 0.0, "netp": 0.0, "mdd": 0.0}
    feats.index = _ensure_utc_idx(pd.DatetimeIndex(feats.index))
    idx = feats.index
    close = feats["Close"].astype(float)
    emaf = close.ewm(span=int(par.ema_fast), adjust=False).mean()
    emas = close.ewm(span=int(par.ema_slow), adjust=False).mean()

    def _rsi_local(c: pd.Series, p: int) -> pd.Series:
        d = c.diff()
        up = d.clip(lower=0.0); dn = (-d.clip(upper=0.0))
        ag = up.ewm(alpha=1.0/p, adjust=False).mean()
        al = dn.ewm(alpha=1.0/p, adjust=False).mean()
        rs = ag/(al+1e-12)
        return 100.0 - (100.0/(1.0+rs))
    rsi = _rsi_local(close, int(par.rsi_period))

    if "atr14" in feats.columns:
        atr_pts = (feats["atr14"] / par.point).astype(float)
    else:
        atr_pts = (close.pct_change().rolling(14, min_periods=5).std().fillna(0.0) * 800.0)

    roll = 500 if len(close) > 1000 else max(200, len(close)//4 or 200)
    rsi_lo_dyn = rsi.rolling(roll, min_periods=100).quantile(0.35).fillna(par.rsi_buy_max)
    rsi_hi_dyn = rsi.rolling(roll, min_periods=100).quantile(0.80).fillna(par.rsi_sell_max)

    last_ts: Optional[pd.Timestamp] = None
    day_cnt: Dict[str, int] = {}
    open_pos: List[dict] = []
    bal = 10_000.0
    pnl: List[float] = []

    reasons = dict(signal_none=0, rsi_block=0, spread_gate=0, gap_gate=0, daily_gate=0, pos_open=0)
    entries = 0

    base_gap = par.min_trade_gap_sec
    base_daily = par.max_trades_per_day
    base_max_spread = par.max_spread_pts

    bars_no_trade = 0
    relax_tick_every = 8
    relax_steps = 0

    def _current_relax():
        return {
            "gap": max(1, base_gap - 1*relax_steps),
            "daily": min(200, base_daily + 10*relax_steps),
            "max_spread": min(900, base_max_spread + 40*relax_steps),
            "eps_mult": 1.0 + 0.5*relax_steps,
            "rsi_buy_pad": 2*relax_steps,
            "rsi_sell_pad": 2*relax_steps,
            "allow_breakout": relax_steps >= 2
        }

    def lots(sl_pts: float) -> float:
        if sl_pts <= 0:
            return 0.01
        money = bal * (par.risk_pct / 100.0) - par.commission
        vpl = sl_pts * 1.0
        return max(0.01, round(max(0.0, money) / max(vpl, 1e-9), 2))

    for i in range(2, len(idx)):
        ts = pd.Timestamp(idx[i]).tz_convert("UTC")
        mid = float(close.iloc[i])
        spr_pts = spread_for_hour(ts.hour, par.spread_pts, spread_hourly)
        spr = spr_pts * par.point
        bid = mid - spr / 2.0
        ask = mid + spr / 2.0

        new_open = []
        closed_trade_this_bar = False
        for d in open_pos:
            d["age"] = d.get("age", 0) + 1
            if d["dir"] > 0:
                gain = (bid - d["open"]) / par.point
                if gain >= par.be_trig:
                    d["sl"] = max(d["sl"], d["open"] + par.be_offs * par.point)
                if gain > par.ts_start:
                    d["sl"] = max(d["sl"], bid - (gain - par.ts_step) * par.point)
                hit_sl = (bid <= d["sl"]) if d["sl"] > 0 else False
                hit_tp = (bid >= d["tp"]) if d["tp"] > 0 else False
            else:
                gain = (d["open"] - ask) / par.point
                if gain >= par.be_trig:
                    d["sl"] = min(d["sl"] if d["sl"] else 1e18, d["open"] - par.be_offs * par.point)
                if gain > par.ts_start:
                    d["sl"] = min(d["sl"] if d["sl"] else 1e18, ask + (gain - par.ts_step) * par.point)
                hit_sl = (ask >= d["sl"]) if d["sl"] > 0 else False
                hit_tp = (ask <= d["tp"]) if d["tp"] > 0 else False

            if d["age"] > par.max_hold_bars:
                px = bid if d["dir"] > 0 else ask
                pts = ((px - d["open"]) / par.point) if d["dir"] > 0 else ((d["open"] - px) / par.point)
                gross = pts * 1.0 * d["lots"]
                cost = spr_pts * 1.0 * d["lots"] + par.commission * d["lots"]
                pr = gross - cost
                bal += pr; pnl.append(pr); closed_trade_this_bar = True
                continue

            if hit_sl or hit_tp:
                px = d["sl"] if hit_sl else d["tp"]
                px += (par.slippage_pts * par.point) * (+1 if d["dir"]>0 and hit_tp else -1 if d["dir"]>0 else -1 if hit_tp else +1)
                pts = ((px - d["open"]) / par.point) if d["dir"] > 0 else ((d["open"] - px) / par.point)
                gross = pts * 1.0 * d["lots"]
                cost = spr_pts * 1.0 * d["lots"] + par.commission * d["lots"]
                pr = gross - cost
                bal += pr; pnl.append(pr); closed_trade_this_bar = True
            else:
                new_open.append(d)

        if closed_trade_this_bar:
            bars_no_trade = 0
        else:
            bars_no_trade += 1
            if bars_no_trade > relax_tick_every and (bars_no_trade % relax_tick_every == 1):
                relax_steps += 1
                if verbose:
                    print(f"[adapt] relax -> {relax_steps}", flush=True)

        relax = _current_relax()

        f_now = float(emaf.iloc[i]); f_prev = float(emaf.iloc[i-1])
        s_now = float(emas.iloc[i]); s_prev = float(emas.iloc[i-1])
        rv = float(rsi.iloc[i])

        eps = 2e-4 * relax["eps_mult"]
        cross_up   = (f_prev <= s_prev and f_now > s_now)
        cross_down = (f_prev >= s_prev and f_now < s_now)
        near_up    = (f_now >= s_now*(1 - eps) and f_now > s_now)
        near_down  = (f_now <= s_now*(1 + eps) and f_now < s_now)

        if par.cross_confirm > 0 and i - par.cross_confirm >= 1:
            f_conf_prev = float(emaf.iloc[i-par.cross_confirm])
            s_conf_prev = float(emas.iloc[i-par.cross_confirm])
            cross_up   = cross_up   and (f_conf_prev <= s_conf_prev)
            cross_down = cross_down and (f_conf_prev >= s_conf_prev)

        rsi_buy_thr  = min(par.rsi_buy_max + relax["rsi_buy_pad"],  92)
        rsi_buy_dyn  = float(rsi_lo_dyn.iloc[i]) + relax["rsi_buy_pad"]
        rsi_buy_ok   = rv <= min(rsi_buy_thr, max(20.0, rsi_buy_dyn))

        rsi_sell_thr = max(par.rsi_sell_max - relax["rsi_sell_pad"], 65)
        rsi_sell_dyn = float(rsi_hi_dyn.iloc[i]) - relax["rsi_sell_pad"]
        rsi_sell_ok  = rv >= max(rsi_sell_thr, min(75.0, rsi_sell_dyn))

        buy_signal  = (cross_up or near_up)   and rsi_buy_ok
        sell_signal = (cross_down or near_down) and rsi_sell_ok

        if relax_steps >= 2:
            buy_signal  = buy_signal  or (f_now > s_now and rv <= rsi_buy_thr + 10)
            sell_signal = sell_signal or (f_now < s_now and rv >= rsi_sell_thr - 10)
        if relax_steps >= 4:
            buy_signal  = buy_signal  or (f_now > s_now)
            sell_signal = sell_signal or (f_now < s_now)

        d = 0
        if buy_signal: d = +1
        if sell_signal: d = -1

        if d != 0 and spr_pts > relax["max_spread"]:
            reasons["spread_gate"] += 1; d = 0
        if d != 0 and last_ts is not None and (ts - last_ts).total_seconds() < relax["gap"]:
            reasons["gap_gate"] += 1; d = 0
        if d != 0:
            k = ts.date().isoformat()
            if day_cnt.get(k, 0) + 1 > relax["daily"]:
                reasons["daily_gate"] += 1; d = 0
        if d != 0 and len(new_open) > 0:
            reasons["pos_open"] += 1; d = 0

        if d == 0:
            open_pos = new_open
            continue

        atr_now = float(atr_pts.iloc[i]) if not pd.isna(atr_pts.iloc[i]) else 800.0
        sl_pts = max(par.atr_mult * atr_now, max(10.0, 2.2 * spr_pts))
        tp_pts = max(10.0, sl_pts * par.rr)
        l = lots(sl_pts)
        if l <= 0:
            open_pos = new_open
            continue

        open_px = (ask if d > 0 else bid) + (par.slippage_pts * par.point) * (+1 if d>0 else -1)
        pos = {
            "dir": d,
            "open": open_px,
            "sl": (open_px - sl_pts * par.point) if d > 0 else (open_px + sl_pts * par.point),
            "tp": (open_px + tp_pts * par.point) if d > 0 else (open_px - tp_pts * par.point),
            "lots": l,
            "age": 0,
        }
        new_open.append(pos)
        open_pos = new_open
        last_ts = ts
        bars_no_trade = 0
        entries += 1
        k = ts.date().isoformat()
        day_cnt[k] = day_cnt.get(k, 0) + 1

    if len(open_pos) > 0 and len(idx) > 0:
        ts = pd.Timestamp(idx[-1]).tz_convert("UTC")
        mid = float(close.iloc[-1])
        spr_pts = spread_for_hour(ts.hour, par.spread_pts, spread_hourly)
        bid = mid - (spr_pts * par.point) / 2.0
        ask = mid + (spr_pts * par.point) / 2.0
        for d in open_pos:
            px = bid if d["dir"] > 0 else ask
            pts = ((px - d["open"]) / par.point) if d["dir"] > 0 else ((d["open"] - px) / par.point)
            gross = pts * 1.0 * d["lots"]
            cost = spr_pts * 1.0 * d["lots"] + par.commission * d["lots"]
            pnl.append(gross - cost)

    pnl = np.array(pnl, dtype=float)
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    pf = (wins.sum() / (-losses.sum())) if losses.sum() < 0 else 0.0
    trades = int((pnl != 0).sum())
    winrate = (100.0 * len(wins) / trades) if trades > 0 else 0.0

    eq = pnl.cumsum()
    peak = -1e18; mdd = 0.0
    for v in eq:
        if v > peak: peak = v
        mdd = max(mdd, peak - v)

    if verbose:
        try:
            print(f"[diag] entries={entries} reasons={reasons}")
        except Exception:
            pass

    return {
        "trades": trades,
        "pf": float(pf),
        "winrate": float(round(winrate, 2)),
        "netp": float(pnl.sum()),
        "mdd": float(round(mdd, 2)),
        "diag": {"entries": int(entries), **{k:int(v) for k,v in reasons.items()}}
    }

# ========= الهدف =========
def score_objective(m_tr: Dict, m_te: Dict,
                    w_pf: float, w_wr: float, w_trades: float, w_dd: float,
                    min_trades: int) -> float:
    pf = max(0.0, _safe_float(m_te.get("pf",0)))
    wr = max(0.0, _safe_float(m_te.get("winrate",0))) / 100.0
    tr_n = max(0, int(m_te.get("trades",0)))
    tr_bonus = min(1.0, tr_n / max(1.0, float(min_trades)))
    dd = max(0.0, _safe_float(m_te.get("mdd",0)))
    drift = max(0.0, _safe_float(m_tr.get("pf",0)) - _safe_float(m_te.get("pf",0)))
    penalty = 0.2 * drift
    few_trades_pen = 0.0
    if tr_n < max(8, min_trades // 3):
        few_trades_pen = 0.5
    return (w_pf*pf + w_wr*wr + w_trades*tr_bonus - w_dd*dd) - penalty - few_trades_pen

# ========= Walk-Forward =========
def split_walk_forward(df: pd.DataFrame, n_windows: int = 4) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    m = df.copy()
    m["Time"] = pd.to_datetime(m["Time"], utc=True, errors="coerce")
    m = m.dropna(subset=["Time"]).sort_values("Time")
    idx = _ensure_utc_idx(pd.DatetimeIndex(m["Time"]))
    if len(idx) < 500:
        return []
    n = len(idx)
    step = n // (n_windows + 1)
    out = []
    for i in range(n_windows):
        train_start = pd.Timestamp(idx[i * step]).tz_convert("UTC")
        train_end = pd.Timestamp(idx[(i + 1) * step - 1]).tz_convert("UTC")
        test_start = pd.Timestamp(idx[(i + 1) * step]).tz_convert("UTC")
        test_end = pd.Timestamp(idx[min((i + 2) * step - 1, n - 1)]).tz_convert("UTC")
        out.append((train_start, train_end, test_start, test_end))
    return out

def slice_df(df: pd.DataFrame, s: pd.Timestamp, e: pd.Timestamp) -> pd.DataFrame:
    m = df.copy()
    m["Time"] = pd.to_datetime(m["Time"], utc=True, errors="coerce")
    m = m.dropna(subset=["Time"]).sort_values("Time")
    s = pd.Timestamp(s).tz_convert("UTC")
    e = pd.Timestamp(e).tz_convert("UTC")
    return m[(m["Time"] >= s) & (m["Time"] <= e)].reset_index(drop=True)
def make_grid_logger(csv_path: Path):
    _mkdir(csv_path)
    if not csv_path.exists():
        csv_path.write_text(
            "loop,ts,phase,rsi_buy_max,rsi_sell_max,atr_mult,rr,ts_start,ts_step,be_trig,be_offs,risk_pct,spread_pts,commission,point,digits,min_trade_gap_sec,max_trades_per_day,max_spread_pts,slippage_pts,ema_fast,ema_slow,rsi_period,cross_confirm,trades,winrate,pf,netp,mdd,score\n",
            encoding="utf-8",
        )

    def _log(loop_no: int, phase: str, params: Dict, metrics: Dict, score_val: float):
        row = [
            str(loop_no), _now_iso(), str(phase),
            str(params.get("rsi_buy_max", "")), str(params.get("rsi_sell_max", "")), str(params.get("atr_mult", "")),
            str(params.get("rr", "")), str(params.get("ts_start", "")), str(params.get("ts_step", "")),
            str(params.get("be_trig", "")), str(params.get("be_offs", "")), str(params.get("risk_pct", "")),
            str(params.get("spread_pts", 180.0)), str(params.get("commission", 7.0)),
            str(params.get("point", 0.01)), str(params.get("digits", 2)),
            str(params.get("min_trade_gap_sec", 5)), str(params.get("max_trades_per_day", 20)),
            str(params.get("max_spread_pts", 600)), str(params.get("slippage_pts", 0.0)),
            str(params.get("ema_fast", 12)), str(params.get("ema_slow", 26)),
            str(params.get("rsi_period", 14)), str(params.get("cross_confirm", 0)),
            str(metrics.get("trades", 0)), str(metrics.get("winrate", 0.0)), str(metrics.get("pf", 0.0)),
            str(metrics.get("netp", 0.0)), str(metrics.get("mdd", 0.0)), f"{float(score_val):.6f}",
        ]
        with open(csv_path, "a", encoding="utf-8") as f:
            try:
                f.write(",".join(map(str, row)) + "\n")
            except Exception as e:
                print("[grid][write][err]", type(e).__name__, e, flush=True)
                for i, v in enumerate(row):
                    print(f"{i}: {type(v)} -> {repr(v)}", flush=True)
                raise

        try:
            pf = float(metrics.get("pf", 0.0))
            wr = float(metrics.get("winrate", 0.0))
            tr = int(metrics.get("trades", 0))
            print(f"[grid] loop={loop_no} phase={phase} pf={pf:.3f} wr={wr:.1f}% trades={tr} score={float(score_val):.4f}", flush=True)
        except Exception:
            pass

    return _log

# ========= البحث =========
def _random_candidate(ranges: Optional[Dict[str, tuple]] = None) -> P:
    def _safe_range(lo, hi, step, fallback):
        lo = int(lo); hi = int(hi); step = max(1, int(step))
        if hi < lo: lo, hi = hi, lo
        arr = list(range(lo, hi + 1, step))
        return arr if arr else fallback
    r_buy_def      = [75,78,80,82,85,88,90]
    r_sell_max_def = [68,70,72,75,78]
    ema_f_def      = [3,4,5,6,8]
    ema_s_def      = [8,10,12,14,16,18,20]
    rsi_p_def      = [5,6,7,8,9,10]
    r_buy      = r_buy_def
    r_sell_max = r_sell_max_def
    ema_f      = ema_f_def
    ema_s      = ema_s_def
    if ranges:
        if "rsi_buy_max" in ranges:
            lo, hi = ranges["rsi_buy_max"]; r_buy = _safe_range(lo, hi, 5, r_buy_def)
        if "rsi_sell_max" in ranges:
            lo, hi = ranges["rsi_sell_max"]; r_sell_max = _safe_range(lo, hi, 2, r_sell_max_def)
        if "ema_fast" in ranges:
            lo, hi = ranges["ema_fast"]; ema_f = _safe_range(lo, hi, 1, ema_f_def)
        if "ema_slow" in ranges:
            lo, hi = ranges["ema_slow"]; ema_s = _safe_range(lo, hi, 1, ema_s_def)
    ef = random.choice(ema_f)
    ema_s_candidates = [s for s in ema_s if s > ef + 1] or ema_s_def
    return P(
        rsi_buy_max=random.choice(r_buy),
        rsi_sell_max=random.choice(r_sell_max),
        atr_mult=random.choice([0.6,0.8,1.0,1.2,1.5,1.8,2.1,2.4]),
        rr=random.choice([1.0,1.2,1.4,1.6,2.0,2.4,2.8,3.2]),
        ts_start=random.choice([40,60,80,120,160,220,280,320]),
        ts_step=random.choice([20,30,40,50,80,120]),
        be_trig=random.choice([10,20,30,50,80,120]),
        be_offs=random.choice([4,6,8,10,15,25]),
        risk_pct=random.choice([0.3,0.6,0.9,1.2,1.5]),
        slippage_pts=random.choice([0,5,10,20]),
        spread_pts=random.choice([120,150,180,220]),
        ema_fast=ef,
        ema_slow=random.choice(ema_s_candidates),
        rsi_period=random.choice(rsi_p_def),
        cross_confirm=random.choice([0,1,2]),
    )

def relax_params(par: P, level: int) -> P:
    par = P(**asdict(par))
    L = max(1, int(level))
    par.rsi_buy_max  = min(92, par.rsi_buy_max + 4*L)
    par.rsi_sell_max = max(65, par.rsi_sell_max - 4*L)
    par.ema_fast = max(3, par.ema_fast - 1*L)
    par.ema_slow = max(par.ema_fast + 2, par.ema_slow - 2*L)
    par.rsi_period = max(5, par.rsi_period - 1*L)
    par.min_trade_gap_sec = max(1, par.min_trade_gap_sec - 1*L)
    par.max_trades_per_day = min(150, par.max_trades_per_day + 12*L)
    par.max_spread_pts = min(900, par.max_spread_pts + 40*L)
    par.spread_pts = max(100.0, par.spread_pts - 10*L)
    par.atr_mult = max(0.6, par.atr_mult - 0.15*L)
    par.ts_start = max(30, par.ts_start - 25*L)
    par.ts_step  = max(15, par.ts_step  - 10*L)
    par.be_trig  = max(10, par.be_trig  - 10*L)
    par.be_offs  = max(4,  par.be_offs  - 1*L)
    par.rr = max(1.0, par.rr - 0.1*L)
    return par

def _eval_candidate(args):
    df_train, df_test, par_dict, spread_hourly, w = args
    par = P(**par_dict)
    m_tr = run_bt(df_train, par, spread_hourly=spread_hourly)
    m_te = run_bt(df_test, par, spread_hourly=spread_hourly)
    sc = score_objective(m_tr, m_te, w["pf"], w["wr"], w["trades"], w["dd"], w["min_trades"])
    return par_dict, m_tr, m_te, sc

def objective_random(df_train: pd.DataFrame, df_test: pd.DataFrame,
                     min_trades: int, tries: int, grid_logger, loop_no: int,
                     weights: Dict, jobs: int,
                     spread_hourly: Optional[pd.Series], verbose: bool,
                     ranges: Optional[Dict[str,tuple]] = None) -> P:
    best = None; best_key = -1e9
    cand = [_random_candidate(ranges) for _ in range(tries)]
    if jobs is None or jobs == 1:
        for par in cand:
            m_tr = run_bt(df_train, par, spread_hourly=spread_hourly)
            m_te = run_bt(df_test, par, spread_hourly=spread_hourly)
            sc = score_objective(m_tr, m_te, weights["pf"], weights["wr"], weights["trades"], weights["dd"], min_trades)
            grid_logger(loop_no, "random", asdict(par), m_te, sc)
            if sc > best_key:
                best_key, best = sc, par
    else:
        with mp.Pool(None if jobs == -1 else jobs) as pool:
            it = [
                (df_train, df_test, asdict(p), spread_hourly, {**weights, "min_trades": min_trades})
                for p in cand
            ]
            for par_dict, m_tr, m_te, sc in pool.imap_unordered(_eval_candidate, it):
                grid_logger(loop_no, "random", par_dict, m_te, sc)
                if sc > best_key:
                    best_key, best = sc, P(**par_dict)
    return best

def objective_hyperopt(df_train: pd.DataFrame, df_test: pd.DataFrame,
                       min_trades: int, tries: int, grid_logger, loop_no: int,
                       weights: Dict, spread_hourly: Optional[pd.Series], verbose: bool) -> P:
    try:
        from hyperopt import fmin, tpe, hp, Trials, STATUS_OK
    except Exception:
        return objective_random(df_train, df_test, min_trades, tries, grid_logger, loop_no, weights, jobs=1, spread_hourly=spread_hourly, verbose=verbose)
    space = {
        "rsi_buy_max": hp.choice("rsi_buy_max", [55, 60, 65, 70, 75, 80, 85]),
        "rsi_sell_max": hp.choice("rsi_sell_max", [78, 80, 85, 88, 90]),
        "atr_mult": hp.choice("atr_mult", [1.0, 1.2, 1.5, 1.8, 2.1]),
        "rr": hp.choice("rr", [1.4, 1.6, 2.0, 2.4]),
        "ts_start": hp.choice("ts_start", [60, 80, 120, 160, 220, 280]),
        "ts_step": hp.choice("ts_step", [30, 40, 50, 80, 120]),
        "be_trig": hp.choice("be_trig", [20, 30, 50, 80, 120]),
        "be_offs": hp.choice("be_offs", [6, 8, 10, 15, 25]),
        "risk_pct": hp.choice("risk_pct", [0.3, 0.6, 0.9, 1.2]),
        "slippage_pts": hp.choice("slippage_pts", [0, 10, 20]),
        "ema_fast": hp.choice("ema_fast", [5, 8, 10, 12, 14]),
        "ema_slow": hp.choice("ema_slow", [20, 26, 30, 35, 40, 50]),
        "rsi_period": hp.choice("rsi_period", [7, 10, 14, 21]),
        "cross_confirm": hp.choice("cross_confirm", [0, 1, 2])
    }
    trials = Trials()
    best_holder = {"par": None, "loss": 1e9}
    def _obj(x):
        par = P(**x)
        m_tr = run_bt(df_train, par, spread_hourly=spread_hourly)
        m_te = run_bt(df_test, par, spread_hourly=spread_hourly)
        sc = score_objective(m_tr, m_te, weights["pf"], weights["wr"], weights["trades"], weights["dd"], min_trades)
        loss = -sc
        grid_logger(loop_no, "bayes", asdict(par), m_te, sc)
        if loss < best_holder["loss"]:
            best_holder["par"] = par; best_holder["loss"] = loss
        return {"loss": loss, "status": STATUS_OK}
    fmin(_obj, space=space, algo=tpe.suggest, max_evals=int(tries), trials=trials, rstate=np.random.default_rng(42))
    return best_holder["par"] or objective_random(df_train, df_test, min_trades, tries, grid_logger, loop_no, weights, jobs=1, spread_hourly=spread_hourly, verbose=verbose)

# ========= تنظيم WFO =========
def walk_forward_optimize(df: pd.DataFrame, n_windows: int, min_trades: int,
                          use_bayes: bool, tries: int, grid_logger,
                          expand: bool, max_expand: int, timeout_min: int,
                          patience: int, weights: Dict, jobs: int,
                          spread_hourly: Optional[pd.Series], verbose: bool) -> List[Dict]:
    wins = split_walk_forward(df, n_windows=n_windows)
    ranges_all = data_driven_ranges(df)
    if not wins:
        m = df.copy()
        m["Time"] = pd.to_datetime(m["Time"], utc=True, errors="coerce")
        m = m.dropna(subset=["Time"]).sort_values("Time")
        n = len(m); cut = int(n * 0.8)
        wins = [(pd.Timestamp(m["Time"].iloc[0]).tz_convert("UTC"),
                 pd.Timestamp(m["Time"].iloc[cut - 1]).tz_convert("UTC"),
                 pd.Timestamp(m["Time"].iloc[cut]).tz_convert("UTC"),
                 pd.Timestamp(m["Time"].iloc[-1]).tz_convert("UTC"))]
    results: List[Dict] = []
    loop_no = 1
    for (tr_s, tr_e, te_s, te_e) in wins:
        df_tr = slice_df(df, tr_s, tr_e)
        df_te = slice_df(df, te_s, te_e)
        start_time = time.time()
        best_sc = -1e9
        last_improve = 0
        expand_count = 0
        while True:
            w_tmp = dict(weights)
            if use_bayes:
                par = objective_hyperopt(
                    df_tr, df_te, min_trades=min_trades, tries=tries,
                    grid_logger=grid_logger, loop_no=loop_no,
                    weights=w_tmp, spread_hourly=spread_hourly, verbose=verbose
                )
            else:
                par = objective_random(
                    df_tr, df_te, min_trades=min_trades, tries=tries,
                    grid_logger=grid_logger, loop_no=loop_no,
                    weights=w_tmp, jobs=jobs, spread_hourly=spread_hourly, verbose=verbose,
                    ranges=ranges_all
                )
            m_tr = run_bt(df_tr, par, spread_hourly=spread_hourly)
            m_te = run_bt(df_te, par, spread_hourly=spread_hourly)
            if (m_tr["trades"] < 5) and (m_te["trades"] < 5):
                if verbose: print("[auto] few trades → relax", flush=True)
                for relax_level in range(1, 6):
                    par = relax_params(par, relax_level)
                    m_tr = run_bt(df_tr, par, spread_hourly=spread_hourly)
                    m_te = run_bt(df_te, par, spread_hourly=spread_hourly)
                    if (m_tr["trades"] >= max(5, min_trades // 2)) or (m_te["trades"] >= max(4, min_trades // 3)):
                        if verbose: print(f"[auto] trades after relax_level={relax_level}", flush=True)
                        break
            m_tr = run_bt(df_tr, par, spread_hourly=spread_hourly)
            m_te = run_bt(df_te, par, spread_hourly=spread_hourly)
            if (m_tr["trades"] < 2 and m_te["trades"] < 2):
                if verbose: print("[rescue] permissive mode", flush=True)
                par = relax_params(par, 5)
                par.ema_fast = max(3, par.ema_fast - 3)
                par.ema_slow = max(par.ema_fast + 2, par.ema_slow - 8)
                par.rsi_period = max(5, par.rsi_period - 5)
                par.rsi_buy_max = min(92, par.rsi_buy_max + 10)
                par.rsi_sell_max = max(65, par.rsi_sell_max - 10)
                par.min_trade_gap_sec = 1
                par.max_trades_per_day = max(par.max_trades_per_day, 120)
                par.max_spread_pts = max(par.max_spread_pts, 800)
                par.spread_pts = max(100.0, par.spread_pts - 40)
                m_tr = run_bt(df_tr, par, spread_hourly=spread_hourly)
                m_te = run_bt(df_te, par, spread_hourly=spread_hourly)
            discover = (m_tr["trades"] < max(10, min_trades // 2)) or (m_te["trades"] < max(8, min_trades // 3))
            if discover:
                w_tmp["trades"] = max(w_tmp.get("trades", 0.6), 2.0)
                w_tmp["pf"] = w_tmp.get("pf", 1.0) * 0.5
                tries = min(int(tries + 200), 2000)
                par = relax_params(par, expand_count + 1)
            sc = score_objective(m_tr, m_te, w_tmp["pf"], w_tmp["wr"], w_tmp["trades"], w_tmp["dd"], min_trades)
            if sc > best_sc + 1e-6:
                best_sc = sc; last_improve = 0
            else:
                last_improve += 1
            elapsed_min = (time.time() - start_time) / 60.0
            trades_ok_train = m_tr["trades"] >= min_trades
            trades_ok_test  = m_te["trades"] >= max(10, min_trades // 2)
            if expand and (not trades_ok_train or not trades_ok_test):
                if (expand_count < max_expand) and (elapsed_min < timeout_min) and (last_improve < patience):
                    level = expand_count + 1
                    if verbose:
                        print(f"[auto] expand {level}/{max_expand} tr/te={m_tr['trades']}/{m_te['trades']}", flush=True)
                    tries = min(int(tries + 250), 2500)
                    par = relax_params(par, level)
                    expand_count += 1
                    loop_no += 1
                    continue
            if (elapsed_min >= timeout_min) or (expand_count >= max_expand) or (last_improve >= patience):
                if verbose:
                    print(f"[auto] stop window elapsed={elapsed_min:.1f} expand={expand_count} patience={last_improve} best={best_sc:.4f}", flush=True)
                results.append({
                    "train": [tr_s.isoformat(), tr_e.isoformat(), m_tr],
                    "test":  [te_s.isoformat(), te_e.isoformat(), m_te],
                    "params": asdict(par),
                })
                break
            if (not expand) or (trades_ok_train and trades_ok_test):
                results.append({
                    "train": [tr_s.isoformat(), tr_e.isoformat(), m_tr],
                    "test":  [te_s.isoformat(), te_e.isoformat(), m_te],
                    "params": asdict(par),
                })
                break
    return results

def aggregate_results(res: List[Dict]) -> Tuple[Dict, Dict]:
    best = None; best_key = -1e9
    for r in res:
        mt = r["test"][2]; tr = r["train"][2]
        trades_ok = 1.0 if mt["trades"] >= 20 else 0.0
        score = (max(0.0, mt["pf"])) * 1.2 + (mt["winrate"]/100.0) * 0.4 + trades_ok * 0.6 - (mt["mdd"]/10000.0)
        drift = max(0.0, (tr["pf"] - mt["pf"]))
        score -= drift * 0.2
        if score > best_key:
            best_key=score; best=r
    pf = np.mean([x["test"][2]["pf"] for x in res]) if res else 0.0
    wr = np.mean([x["test"][2]["winrate"] for x in res]) if res else 0.0
    dd = np.mean([x["test"][2]["mdd"] for x in res]) if res else 0.0
    trades = int(np.sum([x["test"][2]["trades"] for x in res])) if res else 0
    agg={"pf": float(round(pf,3)),"winrate": float(round(wr,2)),"mdd": float(round(dd,2)),"trades": trades}
    return best, agg

def write_live_config(par: Dict, out: Path, out_shadow: bool = True):
    obj = {
        "version": 1,
        "updated_at": _now_iso(),
        "shadow": bool(out_shadow),
        "ai_min_confidence": 0.60,
        "rr": float(par["rr"]),
        "risk_pct": float(par["risk_pct"]),
        "max_spread_pts": int(par.get("max_spread_pts", 350)),
        "ts_start": int(par["ts_start"]),
        "ts_step": int(par["ts_step"]),
        "be_trig": int(par["be_trig"]),
        "be_offs": int(par["be_offs"]),
        "max_open_per_symbol": 1,
        "max_trades_per_day": int(par.get("max_trades_per_day", 20)),
        "use_calendar": True,
        "cal_no_trade_before_min": 5,
        "cal_no_trade_after_min": 5,
        "cal_min_impact": 2,
        "params": {
            "AI_MinConfidence": 0.60,
            "InpRR": float(par["rr"]),
            "NewsFilterLevel": "MEDIUM",
        },
        "notes": "auto by optimize_auto.py",
    }
    _mkdir(out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("[live_config] wrote:", out, "| shadow=", out_shadow, flush=True)

def write_status(status_path: Path, payload: Dict):
    _mkdir(status_path)
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def can_promote(agg: Dict, args, oos_pass_count: int, last_promote_ts_path: Path) -> bool:
    if agg["pf"] < args.commit_threshold_pf: return False
    if agg["trades"] < args.commit_min_trades: return False
    if agg["mdd"] > args.veto_max_dd: return False
    if oos_pass_count < args.min_oos_pass: return False
    if last_promote_ts_path.exists():
        last = pd.to_datetime(last_promote_ts_path.read_text().strip(), utc=True, errors="coerce")
        if last is not None and pd.notna(last):
            gap_h = (pd.Timestamp.utcnow() - last).total_seconds()/3600.0
            if gap_h < args.min_promote_gap_hours:
                return False
    return True

def main():
    ap = argparse.ArgumentParser(description="Auto Walk-Forward Optimizer")
    ap.add_argument("--price", default="data\\XAUUSD_H1.csv")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--source", default="csv", choices=["csv","yfinance","mt5"])
    ap.add_argument("--fetch_if_missing", type=int, default=1)
    ap.add_argument("--mt5_symbol", default="")
    ap.add_argument("--mt5_timeframe", default="H1")
    ap.add_argument("--mt5_bars", type=int, default=100000)
    ap.add_argument("--min_fetch_gap_min", type=int, default=15)
    ap.add_argument("--clean_price_csv", type=int, default=1)
    ap.add_argument("--mt5_auto", type=int, default=1)

    ap.add_argument("--outdir", default="artifacts")
    ap.add_argument("--windows", type=int, default=4)
    ap.add_argument("--min_trades", type=int, default=20)
    ap.add_argument("--use_bayes", type=int, default=1)
    ap.add_argument("--tries", type=int, default=200)
    ap.add_argument("--expand", type=int, default=1)
    ap.add_argument("--max_expand", type=int, default=5)
    ap.add_argument("--timeout_min", type=int, default=12)
    ap.add_argument("--patience", type=int, default=3)

    ap.add_argument("--objective", default="pf,wr,trades,dd")
    ap.add_argument("--w_pf", type=float, default=1.0)
    ap.add_argument("--w_wr", type=float, default=0.4)
    ap.add_argument("--w_trades", type=float, default=0.6)
    ap.add_argument("--w_dd", type=float, default=0.0001)

    ap.add_argument("--commit_threshold_pf", type=float, default=1.2)
    ap.add_argument("--commit_min_trades", type=int, default=60)
    ap.add_argument("--veto_max_dd", type=float, default=1000.0)
    ap.add_argument("--min_oos_pass", type=int, default=2)
    ap.add_argument("--promote_guard_days", type=int, default=7)
    ap.add_argument("--promote_margin", type=float, default=0.05)
    ap.add_argument("--min_promote_gap_hours", type=float, default=4.0)

    ap.add_argument("--daemon", type=int, default=0)
    ap.add_argument("--rerun_hours", type=float, default=6.0)
    ap.add_argument("--loops", type=int, default=1)

    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--jobs", type=int, default=-1)
    ap.add_argument("--verbose", type=int, default=1)

    ap.add_argument("--spread_dynamic_csv", default="")
    ap.add_argument("--slippage_pts", type=float, default=0.0)
    ap.add_argument("--diag", type=int, default=1)

    args = ap.parse_args()
    diag = bool(args.diag)

    random.seed(args.seed); np.random.seed(args.seed)

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    ts_tag = time.strftime('%Y%m%d_%H%M%S')
    grid_csv = outdir / f"grid_log_{ts_tag}.csv"
    grid_logger = make_grid_logger(grid_csv)
    runtime = ROOT / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    live_path = runtime / "live_config.json"
    status_path = runtime / "status.json"
    last_promote_ts_path = runtime / "last_promote.txt"

    price_path = Path(args.price)
    cache_clean = outdir / "clean_price.csv"
    df = load_or_fetch_price(price_path, args.symbol, args.source, bool(args.fetch_if_missing),
                             args.mt5_symbol, args.mt5_timeframe, int(args.mt5_bars),
                             int(args.min_fetch_gap_min), cache_clean, verbose=bool(args.verbose))
    if args.clean_price_csv:
        (outdir / f"clean_{ts_tag}.csv").write_text(df.to_csv(index=False))

    if diag:
        try:
            dft = df.copy()
            dft["Time"] = pd.to_datetime(dft["Time"], utc=True, errors="coerce")
            dft = dft.dropna(subset=["Time"]).sort_values("Time")
            print("[data] rows=", len(dft), "from=", dft["Time"].iloc[0], "to=", dft["Time"].iloc[-1])
            print("[data] NA:", dft[["Open","High","Low","Close"]].isna().sum().to_dict())
            print("[data] dt step:", dft["Time"].diff().value_counts().head(5).to_dict())
            vol = dft["Close"].pct_change().rolling(200, min_periods=50).std().median()
            print(f"[data] median_200d_vol={vol:.6f}")
        except Exception as e:
            print("[data][diag] error:", e)

    spread_hourly = load_spread_by_hour(args.spread_dynamic_csv)
    weights = {"pf": args.w_pf, "wr": args.w_wr, "trades": args.w_trades, "dd": args.w_dd}

    cycles = 10**9 if args.daemon else max(1, args.loops)
    cycle_id = 0
    try:
        while cycle_id < cycles:
            cycle_id += 1
            loops_label = "inf" if args.daemon else str(args.loops)
            print(f"[loop] {cycle_id}/{loops_label}", flush=True)

            res = walk_forward_optimize(
                df, n_windows=args.windows, min_trades=args.min_trades,
                use_bayes=bool(args.use_bayes), tries=int(args.tries),
                grid_logger=grid_logger, expand=bool(args.expand),
                max_expand=int(args.max_expand), timeout_min=int(args.timeout_min),
                patience=int(args.patience), weights=weights, jobs=int(args.jobs),
                spread_hourly=spread_hourly, verbose=bool(args.verbose)
            )
            best, agg = aggregate_results(res)

            wf_json = outdir / f"wf_results_{time.strftime('%Y%m%d_%H%M%S')}.json"
            wf_json.write_text(json.dumps({"windows": res, "aggregate": agg, "best": best}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[wf] aggregate:", agg, flush=True)

            if best:
                best_params = dict(best["params"])
                best_params["slippage_pts"] = args.slippage_pts
                write_live_config(best_params, live_path, out_shadow=True)

                oos_pass = sum(1 for r in res if (r["test"][2]["pf"] >= args.commit_threshold_pf and r["test"][2]["trades"] >= args.commit_min_trades and r["test"][2]["mdd"] <= args.veto_max_dd))
                promote_ok = can_promote(agg, args, oos_pass, last_promote_ts_path)

                if promote_ok:
                    write_live_config(best_params, live_path, out_shadow=False)
                    last_promote_ts_path.write_text(_now_iso())
                    print("[commit] promoted live_config (shadow=False)", flush=True)
                else:
                    print("[commit] stayed shadow", flush=True)

                write_status(status_path, {
                    "ts": _now_iso(),
                    "aggregate": agg, "oos_pass": oos_pass,
                    "live_config": str(live_path),
                    "shadow": not promote_ok,
                    "grid_log": str(grid_csv)
                })
            else:
                print("[wf] no best. check data/params", flush=True)
                write_status(status_path, {"ts": _now_iso(), "error": "no_best"})

            print(f"[grid] log saved -> {grid_csv}", flush=True)

            if not args.daemon:
                break
            if args.verbose:
                print(f"[daemon] sleep {args.rerun_hours}h ...", flush=True)
            time.sleep(max(0.0, float(args.rerun_hours)) * 3600.0)
    except KeyboardInterrupt:
        print("\n[exit] interrupted", flush=True)

if __name__ == "__main__":
    mp.freeze_support()
    main()






    