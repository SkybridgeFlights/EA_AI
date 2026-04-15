from __future__ import annotations
import numpy as np
import pandas as pd

def ema(x: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(x)
    out[:] = np.nan
    if len(x) == 0:
        return out
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i-1]
    return out

def rsi(close: np.ndarray, period: int) -> np.ndarray:
    c = np.asarray(close, dtype=float)
    out = np.empty_like(c)
    out[:] = np.nan
    if len(c) < period + 1:
        return out
    diff = np.diff(c)
    gain = np.where(diff > 0, diff, 0.0)
    loss = np.where(diff < 0, -diff, 0.0)

    avg_gain = np.empty(len(c))
    avg_loss = np.empty(len(c))
    avg_gain[:] = np.nan
    avg_loss[:] = np.nan

    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()

    for i in range(period + 1, len(c)):
        avg_gain[i] = (avg_gain[i-1]*(period-1) + gain[i-1]) / period
        avg_loss[i] = (avg_loss[i-1]*(period-1) + loss[i-1]) / period

    rs = avg_gain / (avg_loss + 1e-12)
    out = 100.0 - (100.0 / (1.0 + rs))
    out[:period] = np.nan
    return out

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)

    tr = np.empty_like(c)
    tr[:] = np.nan
    tr[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))

    # Wilder smoothing
    out = np.empty_like(c)
    out[:] = np.nan
    if len(c) < period + 1:
        return out
    out[period] = np.nanmean(tr[1:period+1])
    for i in range(period + 1, len(c)):
        out[i] = (out[i-1]*(period-1) + tr[i]) / period
    return out