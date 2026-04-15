from __future__ import annotations
from typing import Optional, List
import pandas as pd
from datetime import datetime, timezone

def _tf_to_mt5(tf: str):
    import MetaTrader5 as mt5
    tf = tf.upper().strip()
    m = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    if tf not in m:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return m[tf]

def _ensure_symbol(symbol: str) -> str:
    """
    يحاول إيجاد الرمز الصحيح حتى لو كان عند الوسيط لاحقة مثل XAUUSDr / XAUUSDm / XAUUSD.i
    """
    import MetaTrader5 as mt5

    # 1) exact
    info = mt5.symbol_info(symbol)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(symbol, True)
        return symbol

    # 2) search contains
    all_syms = mt5.symbols_get()
    if not all_syms:
        return symbol

    key = symbol.upper()
    cands = [s.name for s in all_syms if key in s.name.upper()]

    # إذا لم يجد، جرّب على XAU (مفيد للذهب)
    if not cands and "XAU" in key:
        cands = [s.name for s in all_syms if "XAU" in s.name.upper()]

    # اختر الأقرب: يبدأ بنفس الكلمة ثم الأقصر
    if cands:
        cands.sort(key=lambda n: (0 if n.upper().startswith(key) else 1, len(n)))
        best = cands[0]
        inf = mt5.symbol_info(best)
        if inf is not None and not inf.visible:
            mt5.symbol_select(best, True)
        return best

    return symbol

def fetch_rates_mt5(symbol: str, timeframe: str, start: str, end: str, utc: bool = True) -> pd.DataFrame:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed. last_error={mt5.last_error()}")

    symbol_real = _ensure_symbol(symbol)

    tf = _tf_to_mt5(timeframe)

    dt_start = datetime.fromisoformat(start)
    dt_end   = datetime.fromisoformat(end)

    if utc:
        dt_start = dt_start.replace(tzinfo=timezone.utc)
        dt_end   = dt_end.replace(tzinfo=timezone.utc)

    rates = mt5.copy_rates_range(symbol_real, tf, dt_start, dt_end)

    if rates is None or len(rates) == 0:
        # اطبع تشخيص واضح قبل الإنهاء
        last = mt5.last_error()
        mt5.shutdown()
        raise RuntimeError(
            f"No rates returned for symbol='{symbol_real}' (requested '{symbol}'), tf={timeframe}, "
            f"start={start}, end={end}. MT5 last_error={last}. "
            f"Check: symbol name, data availability, and that terminal is connected."
        )

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "tickvol"})
    # spread قد لا يكون موجود لبعض السيرفرات، تأكد
    if "spread" not in df.columns:
        df["spread"] = 0
    if "real_volume" not in df.columns:
        df["real_volume"] = 0

    df = df[["time","open","high","low","close","spread","real_volume","tickvol"]].copy()
    df = df.sort_values("time").reset_index(drop=True)

    mt5.shutdown()
    return df

def save_parquet(df: pd.DataFrame, path: str) -> None:
    df.to_parquet(path, index=False)

def load_parquet(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)