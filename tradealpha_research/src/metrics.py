from __future__ import annotations
from typing import Dict, Any
import numpy as np
import pandas as pd
from .backtest_tech import BacktestResult

def max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak * 100.0
    return float(dd.min())

def profit_factor(trades_pnl: np.ndarray) -> float:
    gains = trades_pnl[trades_pnl > 0].sum()
    losses = -trades_pnl[trades_pnl < 0].sum()
    if losses <= 1e-12:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)

def worst_month_pct(monthly_returns: pd.Series) -> float:
    return float(monthly_returns.min()) if len(monthly_returns) else 0.0

def summarize(res: BacktestResult) -> Dict[str, float]:
    pnls = np.array([t.pnl for t in res.trades], dtype=float)
    pf = profit_factor(pnls) if len(pnls) else 0.0
    mdd = max_drawdown_pct(res.equity) if len(res.equity) else 0.0
    wm = worst_month_pct(res.monthly)
    net = float(pnls.sum()) if len(pnls) else 0.0
    ntr = float(len(pnls))
    return {
        "net_profit": net,
        "profit_factor": pf,
        "max_dd_pct": mdd,
        "worst_month_pct": wm,
        "trades": ntr,
    }

def passes_constraints(stats: Dict[str, float], c: Dict[str, Any]) -> bool:
    if stats["max_dd_pct"] < -float(c["max_dd_pct"]):
        return False
    if stats["trades"] < float(c["min_trades"]):
        return False
    if stats["profit_factor"] < float(c["min_pf"]):
        return False
    if stats["worst_month_pct"] < float(c["worst_month_pct"]):
        return False
    return True