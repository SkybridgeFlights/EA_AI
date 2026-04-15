# src/objective.py
from __future__ import annotations

from typing import Dict, Any
import yaml
import optuna

from .metrics import summarize
from .backtest_tech import backtest_tech
from .mt5_symbol_info import get_symbol_spec


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


def make_objective(df, cfg: Dict[str, Any]):
    space = cfg["search_space"]
    constraints = cfg["constraints"]
    run = cfg["run"]
    costs = cfg["costs"]

    symbol = run["symbol"]
    sym_spec = get_symbol_spec(symbol)

    max_dd_limit = float(constraints["max_dd_pct"])
    min_pf = float(constraints["min_pf"])
    min_trades = float(constraints["min_trades"])
    worst_month_limit = float(constraints["worst_month_pct"])

    def objective(trial: optuna.Trial) -> float:
        p = suggest_params(trial, space)
        if not apply_rules(p):
            raise optuna.TrialPruned()

        res = backtest_tech(
            df,
            p,
            costs=costs,
            run={
                "initial_balance": cfg["risk"]["initial_balance"],
                "trade_on_closed_bar": cfg["execution"]["trade_on_closed_bar"],
            },
            spec=sym_spec,
        )

        st = summarize(res)

        # Prune only useless trials early
        if st["trades"] < 20:
            raise optuna.TrialPruned()

        # -------------------------
        # HARD GATES (Company-like)
        # -------------------------
        dd_abs = abs(float(st["max_dd_pct"]))
        if dd_abs > max_dd_limit + 0.01:
            raise optuna.TrialPruned()

        if float(st["profit_factor"]) < min_pf:
            raise optuna.TrialPruned()

        if float(st["trades"]) < min_trades:
            raise optuna.TrialPruned()

        if float(st["worst_month_pct"]) < worst_month_limit:
            raise optuna.TrialPruned()

        # -------------------------
        # Soft ranking inside gates
        # -------------------------
        wm = float(st["worst_month_pct"])     # higher (less negative) is better
        pf = float(st["profit_factor"])
        tr = float(st["trades"])

        penalty = 0.0

        # keep tiny penalties to prefer stronger stability even within gates
        # (optional): if DD close to limit, penalize slightly
        if dd_abs > max_dd_limit * 0.95:
            penalty += 200.0 * (dd_abs - max_dd_limit * 0.95)

        # prefer PF above minimum
        if pf < (min_pf + 0.10):
            penalty += 300.0 * ((min_pf + 0.10) - pf)

        # prefer worst-month well above limit
        if wm < (worst_month_limit + 2.0):
            penalty += 150.0 * ((worst_month_limit + 2.0) - wm)

        # prefer more trades (stability) but mild
        if tr < (min_trades + 100):
            penalty += 0.5 * ((min_trades + 100) - tr)

        score = float(st["net_profit"]) - penalty

        for k, v in st.items():
            trial.set_user_attr(k, float(v))
        trial.set_user_attr("dd_abs", dd_abs)
        trial.set_user_attr("penalty", penalty)

        return float(score)

    return objective