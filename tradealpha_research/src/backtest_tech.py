# src/backtest_tech.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

from .indicators import ema, rsi, atr
from .mt5_symbol_info import SymbolSpec


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    dir: int
    entry: float
    exit: float
    sl: float
    tp: float
    lots: float
    pnl: float
    reason: str


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity: pd.Series
    stats: Dict[str, float]
    monthly: pd.Series


def backtest_tech(
    df: pd.DataFrame,
    p: Dict[str, Any],
    costs: Dict[str, Any],
    run: Dict[str, Any],
    spec: SymbolSpec
) -> BacktestResult:

    # ----- SYMBOL SPEC -----
    point = spec.point if spec.point > 0 else 0.01
    tick_size = spec.tick_size if spec.tick_size > 0 else point
    tick_value = spec.tick_value if spec.tick_value > 0 else 1.0
    vol_min = spec.vol_min
    vol_step = spec.vol_step
    vol_max = spec.vol_max

    value_per_price_unit = tick_value / tick_size

    # ----- DATA -----
    t = df["time"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    spread_pts = df["spread"].values

    # ----- INDICATORS -----
    ma_fast = ema(c, int(p["InpMAfast"]))
    ma_slow = ema(c, int(p["InpMAslow"]))
    rsi_v = rsi(c, int(p["InpRSI_Period"]))
    atr_v = atr(h, l, c, int(p["InpATR_Period"]))

    # ----- COSTS -----
    fixed_spread = float(costs.get("fixed_spread_points", 0))
    slippage_pts = float(costs.get("slippage_points", 0))
    commission_per_lot = float(costs.get("commission_per_lot", 0.0))

    # ----- ACCOUNT -----
    balance = float(run.get("initial_balance", 10000))
    equity = balance
    eq_curve = []
    eq_time = []

    risk_pct = float(p["InpRiskPct"])
    max_trades_day = int(p["MaxTradesPerDay"])
    daily_loss_pct = float(p["DailyLossPct"])

    use_be = bool(p["UseBreakEven"])
    use_ts = bool(p["UseTrailingStop"])

    open_trade: Optional[Trade] = None
    trades: List[Trade] = []

    current_day = None
    day_trades = 0
    day_start_equity = equity

    def round_volume(v: float) -> float:
        if vol_step <= 0:
            return max(vol_min, min(v, vol_max))
        steps = round((v - vol_min) / vol_step)
        out = vol_min + steps * vol_step
        return max(vol_min, min(out, vol_max))

    def calc_lots(sl_dist: float) -> float:
        risk_money = equity * (risk_pct / 100.0)
        risk_per_lot = sl_dist * value_per_price_unit
        if risk_per_lot <= 0:
            return 0.0
        lots = risk_money / risk_per_lot
        return round_volume(lots)

    for i in range(len(df)):
        ts = df["time"].iloc[i]
        day = ts.date()

        if current_day != day:
            current_day = day
            day_trades = 0
            day_start_equity = equity

        eq_curve.append(equity)
        eq_time.append(ts)

        if open_trade is not None:
            hit = False
            exit_price = None
            reason = None

            if open_trade.dir == 1:
                if l[i] <= open_trade.sl:
                    hit = True
                    exit_price = open_trade.sl
                    reason = "SL"
                elif h[i] >= open_trade.tp:
                    hit = True
                    exit_price = open_trade.tp
                    reason = "TP"
            else:
                if h[i] >= open_trade.sl:
                    hit = True
                    exit_price = open_trade.sl
                    reason = "SL"
                elif l[i] <= open_trade.tp:
                    hit = True
                    exit_price = open_trade.tp
                    reason = "TP"

            if hit:
                pnl = (exit_price - open_trade.entry) * open_trade.dir
                pnl *= open_trade.lots * value_per_price_unit
                pnl -= commission_per_lot * open_trade.lots

                equity += pnl

                trades.append(
                    Trade(
                        open_trade.entry_time,
                        ts,
                        open_trade.dir,
                        open_trade.entry,
                        exit_price,
                        open_trade.sl,
                        open_trade.tp,
                        open_trade.lots,
                        pnl,
                        reason,
                    )
                )
                open_trade = None

        if open_trade is None:
            if day_trades >= max_trades_day:
                continue

            if np.isnan(ma_fast[i]) or np.isnan(ma_slow[i]) or np.isnan(rsi_v[i]) or np.isnan(atr_v[i]):
                continue

            trend_up = ma_fast[i] > ma_slow[i]
            trend_dn = ma_fast[i] < ma_slow[i]

            buy = trend_up and rsi_v[i] <= float(p["InpRSI_BuyMax"])
            sell = trend_dn and rsi_v[i] >= float(p["InpRSI_SellMin"])

            if not buy and not sell:
                continue

            direction = 1 if buy else -1
            entry = c[i]

            sl_dist = float(p["InpATR_SL_Mult"]) * atr_v[i]
            tp_dist = sl_dist * float(p["InpRR"])

            if direction == 1:
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:
                sl = entry + sl_dist
                tp = entry - tp_dist

            lots = calc_lots(abs(entry - sl))
            if lots <= 0:
                continue

            open_trade = Trade(ts, ts, direction, entry, entry, sl, tp, lots, 0.0, "OPEN")
            day_trades += 1

    eq_series = pd.Series(eq_curve, index=pd.to_datetime(eq_time, utc=True))
    monthly = eq_series.resample("ME").last().pct_change().fillna(0) * 100.0

    return BacktestResult(trades=trades, equity=eq_series, stats={}, monthly=monthly)