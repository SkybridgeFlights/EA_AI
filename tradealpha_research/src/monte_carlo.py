# src/monte_carlo.py  (UPDATED FULL — Block Bootstrap + stronger selection fields)
from __future__ import annotations

import os
import math
import json
import random
from typing import Dict, Any

import numpy as np
import pandas as pd

from .objective_wfo import load_yaml
from .wfo import build_wfo_windows, slice_window
from .backtest_tech import backtest_tech
from .mt5_symbol_info import get_symbol_spec


def equity_stats_from_trades(trades: list, initial_balance: float) -> Dict[str, float]:
    if not trades:
        return {
            "net_profit": 0.0,
            "profit_factor": 0.0,
            "max_dd_pct": 0.0,
            "worst_month_pct": 0.0,
            "trades": 0.0,
        }

    trades = sorted(trades, key=lambda x: x.exit_time)

    eq = initial_balance
    eq_series = []
    ts_series = []

    gross_profit = 0.0
    gross_loss = 0.0

    for tr in trades:
        pnl = float(tr.pnl)
        eq += pnl
        eq_series.append(eq)

        ts = pd.Timestamp(tr.exit_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        ts_series.append(ts)

        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)

    eq_s = pd.Series(eq_series, index=pd.to_datetime(ts_series, utc=True)).sort_index()

    peak = eq_s.cummax()
    dd = (eq_s / peak) - 1.0
    max_dd_pct = float(dd.min() * 100.0)

    monthly = eq_s.resample("ME").last().pct_change().fillna(0.0) * 100.0
    worst_month_pct = float(monthly.min()) if len(monthly) else 0.0

    pf = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    net_profit = float(eq_s.iloc[-1] - initial_balance)

    return {
        "net_profit": net_profit,
        "profit_factor": pf if math.isfinite(pf) else 999.0,
        "max_dd_pct": max_dd_pct,
        "worst_month_pct": worst_month_pct,
        "trades": float(len(trades)),
    }


def monte_carlo_on_trades(
    trades: list,
    initial_balance: float,
    n_iter: int = 300,
    delete_frac: float = 0.05,
    pnl_noise_sigma: float = 0.06,
    seed: int = 42,
    method: str = "block",      # "block" (recommended) | "shuffle"
    block_size: int = 20,       # used for block
) -> pd.DataFrame:
    """
    Monte Carlo on trade PnLs:
      - method="block": block bootstrap (preserves clustering, more realistic than full shuffle)
      - delete_frac: randomly delete trades (execution misses)
      - pnl_noise_sigma: multiplicative noise on PnL (spread/slippage uncertainty)
    """
    rng = random.Random(seed)
    pnls = np.array([float(t.pnl) for t in trades], dtype=float)

    if len(pnls) < 30:
        return pd.DataFrame()

    n = len(pnls)
    del_n = int(round(n * delete_frac))
    np_rng = np.random.default_rng(seed + 1337)

    rows = []

    if method == "block":
        b = max(5, int(block_size))
        blocks = [pnls[i:i + b] for i in range(0, n, b)]
        if len(blocks) < 2:
            method = "shuffle"

    for k in range(n_iter):
        # sample order
        if method == "shuffle":
            idx = list(range(n))
            rng.shuffle(idx)
            pn = pnls[idx].copy()
        else:
            idxb = list(range(len(blocks)))
            rng.shuffle(idxb)
            pn = np.concatenate([blocks[j] for j in idxb]).astype(float)
            pn = pn[:n]

        # delete random subset
        if del_n > 0 and len(pn) > 1:
            drop_idx = set(rng.sample(range(len(pn)), k=min(del_n, len(pn) - 1)))
            pn = np.array([pn[i] for i in range(len(pn)) if i not in drop_idx], dtype=float)

        # pnl noise
        noise = np_rng.normal(loc=0.0, scale=pnl_noise_sigma, size=len(pn))
        pn = pn * (1.0 + noise)

        # equity path
        eq = initial_balance
        peak = initial_balance
        max_dd = 0.0
        gp = 0.0
        gl = 0.0

        for x in pn:
            x = float(x)
            eq += x
            if x >= 0:
                gp += x
            else:
                gl += abs(x)
            if eq > peak:
                peak = eq
            dd = (eq / peak) - 1.0
            if dd < max_dd:
                max_dd = dd

        net = eq - initial_balance
        pf = (gp / gl) if gl > 0 else 999.0

        rows.append(
            {
                "iter": k,
                "net_profit": float(net),
                "profit_factor": float(pf),
                "max_dd_pct": float(max_dd * 100.0),
            }
        )

    return pd.DataFrame(rows)


def load_top_configs(stage3_csv: str, top_n: int = 10) -> pd.DataFrame:
    df = pd.read_csv(stage3_csv).sort_values("score", ascending=False)

    # Keep only unique parameter sets
    param_cols = [c for c in df.columns if c.startswith("Inp")] + [
        "MaxTradesPerDay",
        "DailyLossPct",
        "UseTrailingStop",
        "TS_StartPts",
        "TS_StepPts",
        "UseBreakEven",
        "BE_TriggerPts",
        "BE_OffsetPts",
        "MinTradeGapSec",
    ]
    param_cols = [c for c in param_cols if c in df.columns]
    if param_cols:
        df = df.drop_duplicates(subset=param_cols, keep="first")

    return df.head(top_n).reset_index(drop=True)


def row_to_params(row: pd.Series, search_space: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for k in search_space.keys():
        if k in row:
            v = row[k]
            if isinstance(v, str) and v in ("True", "False"):
                v = (v == "True")
            params[k] = v
    return params


def main():
    cfg = load_yaml("configs/phaseA_search.yaml")

    symbol = cfg["run"]["symbol"]
    tf = cfg["run"]["timeframe"]
    start = cfg["run"]["start"]
    end = cfg["run"]["end"]

    from .data_mt5 import fetch_rates_mt5
    df = fetch_rates_mt5(symbol, tf, start, end, utc=True)

    wfo_cfg = cfg.get("wfo", {})
    train_months = int(wfo_cfg.get("train_months", 18))
    test_months = int(wfo_cfg.get("test_months", 6))
    step_months = int(wfo_cfg.get("step_months", 6))
    min_test_bars = int(wfo_cfg.get("min_test_bars", 200))

    windows = build_wfo_windows(
        df,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
        min_test_bars=min_test_bars,
        time_col="time",
    )

    spec = get_symbol_spec(symbol)
    costs = cfg["costs"]
    initial_balance = float(cfg["risk"]["initial_balance"])
    trade_on_closed = bool(cfg["execution"]["trade_on_closed_bar"])

    stage3_csv = "out_wfo/stage_3_company/results.csv"
    top_cfgs = load_top_configs(stage3_csv, top_n=10)

    out_dir = "out_wfo/stage_4_montecarlo"
    os.makedirs(out_dir, exist_ok=True)

    summary_rows = []

    for i, row in top_cfgs.iterrows():
        params = row_to_params(row, cfg["search_space"])
        cfg_tag = f"cfg_{i+1:02d}"

        all_trades = []
        for w in windows:
            _, test_df = slice_window(df, w, time_col="time")
            if len(test_df) < min_test_bars:
                continue
            res = backtest_tech(
                test_df,
                params,
                costs=costs,
                run={"initial_balance": initial_balance, "trade_on_closed_bar": trade_on_closed},
                spec=spec,
            )
            all_trades.extend(res.trades)

        base_stats = equity_stats_from_trades(all_trades, initial_balance)

        mc = monte_carlo_on_trades(
            all_trades,
            initial_balance=initial_balance,
            n_iter=300,
            delete_frac=0.05,
            pnl_noise_sigma=0.06,
            seed=42 + i,
            method="block",
            block_size=20,
        )

        with open(os.path.join(out_dir, f"{cfg_tag}_params.json"), "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2, ensure_ascii=False)

        if mc is None or mc.empty:
            summary_rows.append(
                {
                    "rank": i + 1,
                    "base_net_profit": base_stats["net_profit"],
                    "base_pf": base_stats["profit_factor"],
                    "base_max_dd_pct": base_stats["max_dd_pct"],
                    "base_worst_month_pct": base_stats["worst_month_pct"],
                    "base_trades": base_stats["trades"],
                    "mc_iters": 0,
                    "mc_net_p05": np.nan,
                    "mc_net_med": np.nan,
                    "mc_dd_p95": np.nan,
                    "mc_pf_p05": np.nan,
                    "mc_pass_rate": np.nan,
                }
            )
            continue

        mc.to_csv(os.path.join(out_dir, f"{cfg_tag}_mc.csv"), index=False)

        net_p05 = float(mc["net_profit"].quantile(0.05))
        net_med = float(mc["net_profit"].quantile(0.50))
        dd_p95 = float(mc["max_dd_pct"].abs().quantile(0.95))
        pf_p05 = float(mc["profit_factor"].quantile(0.05))

        dd_limit = float(cfg["constraints"]["max_dd_pct"])
        pf_limit = float(cfg["constraints"]["min_pf"])
        pass_rate = float(((mc["max_dd_pct"].abs() <= dd_limit) & (mc["profit_factor"] >= pf_limit) & (mc["net_profit"] > 0)).mean())

        summary_rows.append(
            {
                "rank": i + 1,
                "base_net_profit": base_stats["net_profit"],
                "base_pf": base_stats["profit_factor"],
                "base_max_dd_pct": base_stats["max_dd_pct"],
                "base_worst_month_pct": base_stats["worst_month_pct"],
                "base_trades": base_stats["trades"],
                "mc_iters": int(len(mc)),
                "mc_net_p05": net_p05,
                "mc_net_med": net_med,
                "mc_dd_p95": dd_p95,
                "mc_pf_p05": pf_p05,
                "mc_pass_rate": pass_rate,
            }
        )

    summary = pd.DataFrame(summary_rows).sort_values(["mc_pass_rate", "mc_net_med"], ascending=[False, False])
    summary.to_csv(os.path.join(out_dir, "mc_summary.csv"), index=False)
    print("Saved:", os.path.join(out_dir, "mc_summary.csv"))
    print(summary.head(10).to_string(index=False))


if __name__ == "__main__":
    main()