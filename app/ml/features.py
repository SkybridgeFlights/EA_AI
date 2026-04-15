# app/ml/features.py
import numpy as np
import pandas as pd
from typing import Optional


# =========================
# Helpers
# =========================
def _ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize index to UTC DatetimeIndex, sorted, unique."""
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df[~df.index.to_series().isna()]
    df = df[~df.index.duplicated(keep="last")]
    return df.sort_index()


def _to_numeric_series(x, index: pd.Index) -> pd.Series:
    """Convert Series/array-like/scalar to numeric Series aligned to index."""
    if isinstance(x, pd.Series):
        s = x
        if not s.index.equals(index):
            s = s.reindex(index)
        return pd.to_numeric(s, errors="coerce")

    if isinstance(x, (np.ndarray, list, tuple)):
        s = pd.Series(x, index=index)
        return pd.to_numeric(s, errors="coerce")

    try:
        v = float(x)
    except Exception:
        v = np.nan
    return pd.Series(v, index=index, dtype=float)


def _best_series_from_df(df: pd.DataFrame, index: pd.Index) -> pd.Series:
    """Pick best column by non-NaN count using iloc (safe with duplicate labels)."""
    if df is None or df.empty:
        return pd.Series(index=index, dtype=float)

    best_cnt = -1
    best_s = None
    for i in range(df.shape[1]):
        col_s = df.iloc[:, i]
        s = _to_numeric_series(col_s, index)
        cnt = int(s.notna().sum())
        if cnt > best_cnt:
            best_cnt = cnt
            best_s = s

    return best_s if best_s is not None else pd.Series(index=index, dtype=float)


def _extract_field(dp: pd.DataFrame, field: str, fallback: Optional[float] = None) -> pd.Series:
    """
    Robust field extraction:
      - duplicate column names
      - MultiIndex (yfinance)
      - MT5 volume variants (tick_volume/real_volume)
    """
    idx = dp.index
    f = field.lower().strip()
    candidates = [field, f, f.capitalize()]

    if f == "volume":
        candidates += ["tick_volume", "tickvolume", "tick_vol", "real_volume", "realvolume"]

    # direct columns (including duplicates)
    for name in candidates:
        if name in dp.columns:
            obj = dp.loc[:, name]  # Series if unique, DF if duplicates
            if isinstance(obj, pd.DataFrame):
                s = _best_series_from_df(obj, idx)
            else:
                s = _to_numeric_series(obj, idx)
            if s.notna().sum() > 0:
                return s

    # MultiIndex search
    if isinstance(dp.columns, pd.MultiIndex):
        for lvl in range(dp.columns.nlevels):
            try:
                sub = dp.xs(field, axis=1, level=lvl, drop_level=False)
            except Exception:
                continue
            if isinstance(sub, pd.DataFrame):
                s = _best_series_from_df(sub, idx)
                if s.notna().sum() > 0:
                    return s

    if fallback is None:
        return pd.Series(index=idx, dtype=float)
    return pd.Series(float(fallback), index=idx, dtype=float)


# =========================
# Indicators
# =========================
def _ema(x: pd.Series, span: int) -> pd.Series:
    return x.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta.clip(upper=0.0))
    avg_gain = up.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period, min_periods=1).mean()
    md = (tp - sma).abs().rolling(period, min_periods=1).mean()
    return (tp - sma) / (0.015 * (md + 1e-12))


def _stoch_kd(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3):
    ll = low.rolling(k, min_periods=1).min()
    hh = high.rolling(k, min_periods=1).max()
    k_fast = 100.0 * (close - ll) / ((hh - ll) + 1e-12)
    d_slow = k_fast.rolling(d, min_periods=1).mean()
    return k_fast, d_slow


def _time_cyc(index: pd.DatetimeIndex) -> pd.DataFrame:
    idx = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    hour = idx.hour.values
    dow = idx.weekday.values
    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / 24.0),
            "hour_cos": np.cos(2 * np.pi * hour / 24.0),
            "dow_sin": np.sin(2 * np.pi * dow / 7.0),
            "dow_cos": np.cos(2 * np.pi * dow / 7.0),
        },
        index=index,
    )


# =========================
# Feature generation (PRO)
# =========================
def make_features(df_price: pd.DataFrame, df_news: pd.DataFrame) -> pd.DataFrame:
    if df_price is None or df_price.empty:
        return pd.DataFrame()

    dp = _ensure_utc_index(df_price.copy())

    o = _extract_field(dp, "Open")
    h = _extract_field(dp, "High")
    l = _extract_field(dp, "Low")
    c = _extract_field(dp, "Close")
    v = _extract_field(dp, "Volume", fallback=0.0)

    # numeric + fill
    o = pd.to_numeric(o, errors="coerce").ffill().bfill()
    h = pd.to_numeric(h, errors="coerce").ffill().bfill()
    l = pd.to_numeric(l, errors="coerce").ffill().bfill()
    c = pd.to_numeric(c, errors="coerce").ffill().bfill()
    v = pd.to_numeric(v, errors="coerce").fillna(0.0)

    out = pd.DataFrame(index=dp.index)

    # ----- Candle anatomy -----
    rng = (h - l).abs()
    body = (c - o)
    body_abs = body.abs()
    upper_wick = (h - np.maximum(o, c)).clip(lower=0.0)
    lower_wick = (np.minimum(o, c) - l).clip(lower=0.0)

    # NOTE: raw range/body (absolute price units) intentionally excluded —
    # they are non-stationary (Gold $1200→$3000+) and cause overfitting.
    out["range_pct"] = (rng / (c + 1e-12)).clip(0, 0.20)
    out["body_pct"] = (body_abs / (rng + 1e-12)).clip(0, 1.5)
    out["upper_wick_pct"] = (upper_wick / (rng + 1e-12)).clip(0, 1.5)
    out["lower_wick_pct"] = (lower_wick / (rng + 1e-12)).clip(0, 1.5)
    out["close_pos"] = ((c - l) / (rng + 1e-12)).clip(0, 1.0)

    # gap / open-close structure
    prev_c = c.shift(1)
    out["gap_1"] = ((o - prev_c) / (prev_c + 1e-12)).fillna(0.0).clip(-0.10, 0.10)
    out["oc_ret"] = ((c - o) / (o + 1e-12)).fillna(0.0).clip(-0.10, 0.10)

    # ----- Returns / log returns -----
    ret1 = c.pct_change()
    out["ret_1"] = ret1.fillna(0.0)
    out["ret_2"] = c.pct_change(2).fillna(0.0)
    out["ret_5"] = c.pct_change(5).fillna(0.0)
    out["ret_10"] = c.pct_change(10).fillna(0.0)

    logc = np.log(c.clip(lower=1e-12))
    out["logret_1"] = logc.diff().fillna(0.0)
    out["logret_5"] = logc.diff(5).fillna(0.0)

    # momentum z-scores
    m5 = out["ret_5"]
    m10 = out["ret_10"]
    out["mom5_z60"] = ((m5 - m5.rolling(60, min_periods=10).mean()) / (m5.rolling(60, min_periods=10).std() + 1e-12)).fillna(0.0).clip(-8, 8)
    out["mom10_z120"] = ((m10 - m10.rolling(120, min_periods=20).mean()) / (m10.rolling(120, min_periods=20).std() + 1e-12)).fillna(0.0).clip(-8, 8)

    # ----- Volatility -----
    out["vol_std_20"] = ret1.rolling(20, min_periods=2).std().fillna(0.0)
    out["vol_std_60"] = ret1.rolling(60, min_periods=2).std().fillna(0.0)

    atr14 = _atr(h, l, c, 14).bfill().fillna(0.0)
    # atr14 (absolute) excluded — non-stationary; atr_pct is the normalized version
    out["atr_pct"] = (atr14 / (c + 1e-12)).clip(0, 0.2)

    # Parkinson volatility (uses high/low)
    hl_log = np.log((h + 1e-12) / (l + 1e-12))
    out["parkinson20"] = (hl_log.pow(2).rolling(20, min_periods=2).mean() / (4 * np.log(2))).fillna(0.0)

    # ----- Trend / mean reversion -----
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    ema100 = _ema(c, 100)

    # Raw EMA levels excluded — non-stationary price levels cause train/test leakage.
    # Only ATR-normalized distances and slopes are kept.
    out["dist_ema20"] = ((c - ema20) / (atr14 + 1e-12)).clip(-10, 10)
    out["dist_ema50"] = ((c - ema50) / (atr14 + 1e-12)).clip(-10, 10)
    out["dist_ema100"] = ((c - ema100) / (atr14 + 1e-12)).clip(-10, 10)

    # normalized slopes (scale-stable)
    out["ema20_slope_atr"] = (ema20.diff() / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)
    out["ema50_slope_atr"] = (ema50.diff() / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)

    # MACD — normalized by ATR to remove price-level dependency
    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    macd = ema12 - ema26
    macd_sig = _ema(macd, 9)
    out["macd_atr"] = (macd / (atr14 + 1e-12)).clip(-10, 10)
    out["macd_sig_atr"] = (macd_sig / (atr14 + 1e-12)).clip(-10, 10)
    out["macd_hist_atr"] = ((macd - macd_sig) / (atr14 + 1e-12)).clip(-10, 10)

    # ----- Oscillators -----
    out["rsi14"] = _rsi(c, 14).fillna(50.0)
    out["rsi7"] = _rsi(c, 7).fillna(50.0)
    out["cci20"] = _cci(h, l, c, 20).fillna(0.0).clip(-400, 400)

    k_fast, d_slow = _stoch_kd(h, l, c, 14, 3)
    out["stoch_k"] = k_fast.fillna(50.0).clip(0, 100)
    out["stoch_d"] = d_slow.fillna(50.0).clip(0, 100)

    # ----- Breakouts / levels -----
    hh20 = h.rolling(20, min_periods=2).max()
    ll20 = l.rolling(20, min_periods=2).min()
    out["break_up_20"] = ((c - hh20) / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)
    out["break_dn_20"] = ((c - ll20) / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)

    hh50 = h.rolling(50, min_periods=2).max()
    ll50 = l.rolling(50, min_periods=2).min()
    out["break_up_50"] = ((c - hh50) / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)
    out["break_dn_50"] = ((c - ll50) / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)

    # rolling position in range (mean reversion context)
    mid20 = (hh20 + ll20) / 2.0
    out["pos_mid20_atr"] = ((c - mid20) / (atr14 + 1e-12)).fillna(0.0).clip(-10, 10)

    # ----- Volume features -----
    v_mean20 = v.rolling(20, min_periods=2).mean()
    v_std20 = v.rolling(20, min_periods=2).std()
    out["vol_z20"] = ((v - v_mean20) / (v_std20 + 1e-12)).fillna(0.0).clip(-8, 8)
    out["vol_chg1"] = (v.pct_change().replace([np.inf, -np.inf], 0.0)).fillna(0.0).clip(-10, 10)

    # ----- Time features -----
    out = out.join(_time_cyc(out.index))

    # ----- News impact (±30m) -----
    out["news_impact"] = 0.0
    if df_news is not None and not df_news.empty and "time" in df_news.columns:
        dn = df_news.copy()
        dn["time"] = pd.to_datetime(dn["time"], utc=True, errors="coerce")
        dn = dn.dropna(subset=["time"])
        if not dn.empty and "impact" in dn.columns:
            dn = dn.set_index("time").sort_index()
            window = pd.Timedelta(minutes=30)
            for t in out.index:
                win = dn.loc[(dn.index >= t - window) & (dn.index <= t + window)]
                if not win.empty:
                    out.loc[t, "news_impact"] = float(pd.to_numeric(win["impact"], errors="coerce").max())

    out = out.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    return out.astype(float)


# =========================
# Labels (3-class)
# =========================
def make_labels(df_price: pd.DataFrame, horizon: int = 6) -> pd.Series:
    """
    3-class label {-1,0,1} based on future move after N bars with adaptive threshold:
      thr = max(0.05%, 0.25 * ATR%)
    """
    if df_price is None or df_price.empty:
        return pd.Series(dtype=int)

    dp = _ensure_utc_index(df_price.copy())
    h = _extract_field(dp, "High")
    l = _extract_field(dp, "Low")
    c = _extract_field(dp, "Close")

    h = pd.to_numeric(h, errors="coerce").ffill().bfill()
    l = pd.to_numeric(l, errors="coerce").ffill().bfill()
    c = pd.to_numeric(c, errors="coerce").ffill().bfill()

    atr14 = _atr(h, l, c, 14).bfill().fillna(0.0)
    atr_pct = (atr14 / (c + 1e-12)).clip(lower=0.0)
    thr = np.maximum(0.0005, 0.25 * atr_pct.values)  # 0.05% minimum

    future = c.shift(-horizon)
    ret_fwd = (future - c) / (c + 1e-12)

    y = pd.Series(0, index=c.index, dtype=int)
    y[ret_fwd > thr] = 1
    y[ret_fwd < -thr] = -1
    return y
