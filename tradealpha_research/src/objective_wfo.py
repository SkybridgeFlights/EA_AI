# src/objective_wfo.py
from __future__ import annotations

from typing import Dict, Any, List
import yaml
import numpy as np
import optuna
import pandas as pd

from .metrics import summarize
from .backtest_tech import backtest_tech
from .mt5_symbol_info import get_symbol_spec
from .wfo import build_wfo_windows, slice_window


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f.read())


def suggest_params(trial: optuna.Trial, space: Dict[str, Any]) -> Dict[str, Any]:
    p: Dict[str, Any] = {}
    for k, spec in space.items():
        t = spec["type"]
        if t == "int":
            step = spec.get("step", 1)
            p[k] = trial.suggest_int(k, int(spec["low"]), int(spec["high"]), step=int(step))
        elif t == "float":
            step = spec.get("step", None)
            if step is None:
                p[k] = trial.suggest_float(k, float(spec["low"]), float(spec["high"]))
            else:
                p[k] = trial.suggest_float(k, float(spec["low"]), float(spec["high"]), step=float(step))
        elif t == "bool":
            p[k] = trial.suggest_categorical(k, [True, False])
        else:
            raise ValueError(f"Unknown type: {t} for {k}")
    return p


def apply_rules(p: Dict[str, Any]) -> bool:
    if not (p["InpMAslow"] >= p["InpMAfast"] + 10):
        return False
    if not (p["InpRSI_SellMin"] <= p["InpRSI_BuyMax"] - 5):
        return False
    if not (p["TS_StepPts"] <= p["TS_StartPts"]):
        return False
    return True


def make_objective_wfo(df: pd.DataFrame, cfg: Dict[str, Any]):
    """
    WFO-CV objective:
    - Evaluate SAME params across rolling test windows.
    - Hard gate per test window (DD/PF/WorstMonth/Trades).
    - Rank by robust aggregate (median test profit) minus stability penalties.
    """
    space = cfg["search_space"]
    constraints = cfg["constraints"]
    run = cfg["run"]
    costs = cfg["costs"]

    # WFO settings (optional in YAML). Defaults are suitable for ~5y H1.
    wfo_cfg = cfg.get("wfo", {})
    train_months = int(wfo_cfg.get("train_months", 18))
    test_months = int(wfo_cfg.get("test_months", 6))
    step_months = int(wfo_cfg.get("step_months", 6))
    min_test_bars = int(wfo_cfg.get("min_test_bars", 200))
    min_windows = int(wfo_cfg.get("min_windows", 4))

    windows = build_wfo_windows(
        df,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
        min_test_bars=min_test_bars,
        time_col="time",
    )
    if len(windows) < min_windows:
        raise RuntimeError(
            f"Not enough WFO windows ({len(windows)}) for settings "
            f"train={train_months}m test={test_months}m step={step_months}m. "
            f"Expand date range or relax WFO settings."
        )

    symbol = run["symbol"]
    sym_spec = get_symbol_spec(symbol)

    max_dd_limit = float(constraints["max_dd_pct"])
    min_pf = float(constraints["min_pf"])
    min_trades_total = float(constraints["min_trades"])
    worst_month_limit = float(constraints["worst_month_pct"])

    # per-window minimum trades (avoid "passes by luck" on tiny trade counts)
    min_trades_per_window = int(wfo_cfg.get("min_trades_per_window", max(30, int(min_trades_total / len(windows) * 0.6))))

    def objective(trial: optuna.Trial) -> float:
        p = suggest_params(trial, space)
        if not apply_rules(p):
            raise optuna.TrialPruned()

        test_net: List[float] = []
        test_dd: List[float] = []
        test_pf: List[float] = []
        test_wm: List[float] = []
        test_tr: List[float] = []

        # Evaluate across windows
        for w in windows:
            train_df, test_df = slice_window(df, w, time_col="time")

            # Optional: ensure enough data in test
            if len(test_df) < min_test_bars:
                continue

            # Run test backtest (we focus on out-of-sample)
            res_test = backtest_tech(
                test_df,
                p,
                costs=costs,
                run={
                    "initial_balance": cfg["risk"]["initial_balance"],
                    "trade_on_closed_bar": cfg["execution"]["trade_on_closed_bar"],
                },
                spec=sym_spec,
            )
            st = summarize(res_test)

            tr = float(st["trades"])
            dd_abs = abs(float(st["max_dd_pct"]))
            pf = float(st["profit_factor"])
            wm = float(st["worst_month_pct"])
            net = float(st["net_profit"])

            # Hard gates PER TEST WINDOW
            if tr < min_trades_per_window:
                raise optuna.TrialPruned()
            if dd_abs > max_dd_limit + 0.01:
                raise optuna.TrialPruned()
            if pf < min_pf:
                raise optuna.TrialPruned()
            if wm < worst_month_limit:
                raise optuna.TrialPruned()

            test_net.append(net)
            test_dd.append(dd_abs)
            test_pf.append(pf)
            test_wm.append(wm)
            test_tr.append(tr)

        if len(test_net) < min_windows:
            raise optuna.TrialPruned()

        # Robust aggregate score
        net_med = float(np.median(test_net))
        net_mean = float(np.mean(test_net))
        net_std = float(np.std(test_net, ddof=0))

        dd_mean = float(np.mean(test_dd))
        pf_mean = float(np.mean(test_pf))
        wm_mean = float(np.mean(test_wm))
        tr_mean = float(np.mean(test_tr))

        # Stability penalties (prefer consistent forward performance)
        penalty = 0.0
        penalty += 0.20 * net_std                      # penalize unstable profits
        penalty += 50.0 * max(0.0, dd_mean - max_dd_limit * 0.90)  # prefer margin under DD
        penalty += 200.0 * max(0.0, (min_pf + 0.05) - pf_mean)      # prefer PF above minimum
        penalty += 30.0 * max(0.0, (worst_month_limit + 1.0) - wm_mean)  # prefer better worst month

        score = net_med - penalty

        # Attach diagnostics
        trial.set_user_attr("wfo_windows", float(len(test_net)))
        trial.set_user_attr("wfo_net_med", net_med)
        trial.set_user_attr("wfo_net_mean", net_mean)
        trial.set_user_attr("wfo_net_std", net_std)
        trial.set_user_attr("wfo_dd_mean", dd_mean)
        trial.set_user_attr("wfo_pf_mean", pf_mean)
        trial.set_user_attr("wfo_wm_mean", wm_mean)
        trial.set_user_attr("wfo_tr_mean", tr_mean)
        trial.set_user_attr("penalty", float(penalty))

        return float(score)

    return objective