# src/pipeline_auto_wfo_mc.py  (FULL FIXED — added numpy import + robust best_row casting)
from __future__ import annotations

import os
import copy
import math
import json

import numpy as np
import pandas as pd
import yaml
import optuna

from .objective_wfo import make_objective_wfo, load_yaml
from .data_mt5 import fetch_rates_mt5
from .monte_carlo import (
    load_top_configs,
    row_to_params,
    equity_stats_from_trades,
    monte_carlo_on_trades,
)
from .wfo import build_wfo_windows, slice_window
from .backtest_tech import backtest_tech
from .mt5_symbol_info import get_symbol_spec


BASE_STAGES = [
    {"name": "explore", "dd": 18.0, "pf": 1.10, "wm": -12.0, "tr": 120},
    {"name": "tighten", "dd": 15.0, "pf": 1.15, "wm": -10.0, "tr": 160},
    {"name": "company", "dd": 12.0, "pf": 1.20, "wm": -8.0,  "tr": 200},
]


def _snap_float(x: float, step: float, mode: str) -> float:
    if step <= 0:
        return float(x)
    q = x / step
    if mode == "floor":
        return float(math.floor(q) * step)
    if mode == "ceil":
        return float(math.ceil(q) * step)
    return float(round(q) * step)


def _snap_int(x: float, step: int, mode: str) -> int:
    step = int(step) if step else 1
    if step <= 1:
        return int(round(x))
    q = x / step
    if mode == "floor":
        return int(math.floor(q) * step)
    if mode == "ceil":
        return int(math.ceil(q) * step)
    return int(round(q) * step)


def _align_bounds_to_step(spec: dict, lo: float, hi: float) -> tuple[float, float]:
    t = spec["type"]
    step = spec.get("step", None)

    if t == "int":
        st = int(step) if step is not None else 1
        lo2 = _snap_int(lo, st, "ceil")
        hi2 = _snap_int(hi, st, "floor")
        if hi2 < lo2:
            mid = _snap_int((lo + hi) / 2.0, st, "round")
            lo2 = mid
            hi2 = mid
        return float(lo2), float(hi2)

    if t == "float":
        if step is None:
            return float(lo), float(hi)
        st = float(step)
        lo2 = _snap_float(lo, st, "ceil")
        hi2 = _snap_float(hi, st, "floor")
        if hi2 < lo2:
            mid = _snap_float((lo + hi) / 2.0, st, "round")
            lo2 = mid
            hi2 = mid
        return float(lo2), float(hi2)

    return float(lo), float(hi)


def narrow_search_space(df_results: pd.DataFrame, space: dict, keep_ratio: float = 0.25) -> dict:
    new_space = copy.deepcopy(space)

    df_results = df_results.dropna(subset=["score"])
    if len(df_results) < 10:
        return new_space

    top_n = max(5, int(len(df_results) * keep_ratio))
    top = df_results.sort_values("score", ascending=False).head(top_n)

    for name, spec in space.items():
        if name not in top.columns:
            continue
        t = spec["type"]
        if t not in ("int", "float"):
            continue

        base_lo = float(spec["low"])
        base_hi = float(spec["high"])

        lo = float(top[name].min())
        hi = float(top[name].max())
        margin = (hi - lo) * 0.25

        lo = max(base_lo, lo - margin)
        hi = min(base_hi, hi + margin)

        lo, hi = _align_bounds_to_step(spec, lo, hi)

        new_space[name]["low"] = lo if t == "float" else int(lo)
        new_space[name]["high"] = hi if t == "float" else int(hi)

    return new_space


def auto_relax(stage: dict, attempt: int) -> dict:
    s = dict(stage)
    s["dd"] = float(s["dd"]) + 1.5 * (attempt + 1)
    s["pf"] = max(1.05, float(s["pf"]) - 0.03 * (attempt + 1))
    s["wm"] = float(s["wm"]) - 1.0 * (attempt + 1)
    s["tr"] = max(60, int(s.get("tr", 200)) - 20 * (attempt + 1))
    s["name"] = f"{s['name']}_relax{attempt+1}"
    return s


def _write_stage(stage_dir: str, cfg: dict, df_out: pd.DataFrame | None, meta: dict):
    os.makedirs(stage_dir, exist_ok=True)
    with open(os.path.join(stage_dir, "stage_meta.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)
    with open(os.path.join(stage_dir, "effective_config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    if df_out is not None and len(df_out) > 0:
        df_out.to_csv(os.path.join(stage_dir, "results.csv"), index=False)


def run_stage(df: pd.DataFrame, base_cfg: dict, stage_cfg: dict, n_trials: int = 250):
    cfg = copy.deepcopy(base_cfg)
    cfg["constraints"]["max_dd_pct"] = float(stage_cfg["dd"])
    cfg["constraints"]["min_pf"] = float(stage_cfg["pf"])
    cfg["constraints"]["worst_month_pct"] = float(stage_cfg["wm"])
    cfg["constraints"]["min_trades"] = int(stage_cfg.get("tr", cfg["constraints"].get("min_trades", 200)))

    objective = make_objective_wfo(df, cfg)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=int(n_trials), show_progress_bar=True)

    trials = [t for t in study.trials if t.value is not None]
    if not trials:
        return None, None, cfg

    rows = [{"score": t.value, **t.params, **t.user_attrs} for t in trials]
    df_out = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

    new_space = narrow_search_space(df_out, cfg["search_space"], keep_ratio=0.25)
    return df_out, new_space, cfg


def _to_jsonable(x):
    # robust conversion for numpy scalars / pandas scalars
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    return x


def run_stage4_montecarlo(cfg: dict, df: pd.DataFrame, out_root: str, stage3_dir: str):
    stage3_csv = os.path.join(stage3_dir, "results.csv")
    top_cfgs = load_top_configs(stage3_csv, top_n=10)

    out_dir = os.path.join(out_root, "stage_4_montecarlo")
    os.makedirs(out_dir, exist_ok=True)

    symbol = cfg["run"]["symbol"]
    spec = get_symbol_spec(symbol)
    costs = cfg["costs"]
    initial_balance = float(cfg["risk"]["initial_balance"])
    trade_on_closed = bool(cfg["execution"]["trade_on_closed_bar"])

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

    dd_limit = float(cfg["constraints"]["max_dd_pct"])
    pf_limit = float(cfg["constraints"]["min_pf"])

    summary_rows = []
    params_by_rank = {}

    for i, row in top_cfgs.iterrows():
        params = row_to_params(row, cfg["search_space"])
        cfg_tag = f"cfg_{i+1:02d}"
        params_by_rank[i + 1] = params

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
                    "mc_net_p05": float("nan"),
                    "mc_net_med": float("nan"),
                    "mc_dd_p95": float("nan"),
                    "mc_pf_p05": float("nan"),
                    "mc_pass_rate": float("nan"),
                }
            )
            continue

        mc.to_csv(os.path.join(out_dir, f"{cfg_tag}_mc.csv"), index=False)

        net_p05 = float(mc["net_profit"].quantile(0.05))
        net_med = float(mc["net_profit"].quantile(0.50))
        dd_p95 = float(mc["max_dd_pct"].abs().quantile(0.95))
        pf_p05 = float(mc["profit_factor"].quantile(0.05))

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

    summary = pd.DataFrame(summary_rows)
    summary_path = os.path.join(out_dir, "mc_summary.csv")
    summary.to_csv(summary_path, index=False)

    passed = summary.dropna(subset=["mc_dd_p95", "mc_net_p05", "mc_net_med"])
    passed_gate = passed[passed["mc_dd_p95"] <= dd_limit].copy()

    if len(passed_gate) > 0:
        selected = passed_gate.sort_values(
            ["mc_net_p05", "mc_net_med", "mc_pf_p05", "mc_pass_rate"],
            ascending=[False, False, False, False],
        ).iloc[0]
        reason = f"passed_gate(mc_dd_p95<= {dd_limit}) then max(mc_net_p05, mc_net_med)"
    else:
        selected = passed.sort_values(
            ["mc_dd_p95", "mc_net_p05", "mc_net_med"],
            ascending=[True, False, False],
        ).iloc[0]
        reason = f"no_config_passed_gate(mc_dd_p95<= {dd_limit}); picked min(mc_dd_p95) then max(mc_net_p05)"

    sel_rank = int(selected["rank"])
    final_params = params_by_rank.get(sel_rank, {})

    best_row = {k: _to_jsonable(selected[k]) for k in selected.index}

    final = {
        "selected_by": reason,
        "constraints_used": {"dd_limit": dd_limit, "pf_limit": pf_limit},
        "best_row": best_row,
        "params": final_params,
        "symbol": cfg["run"]["symbol"],
        "timeframe": cfg["run"]["timeframe"],
    }

    with open(os.path.join(out_dir, "final_config.json"), "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    print("Saved:", summary_path)
    print(summary.sort_values(["mc_net_p05", "mc_net_med"], ascending=[False, False]).head(10).to_string(index=False))
    print("Saved:", os.path.join(out_dir, "final_config.json"))


if __name__ == "__main__":
    # Note: main() is defined in the earlier versions of this file.
    # This module is meant to be executed as: python -m src.pipeline_auto_wfo_mc
    # The main() function is below.
    def main():
        cfg = load_yaml("configs/phaseA_search.yaml")

        symbol = cfg["run"]["symbol"]
        tf = cfg["run"]["timeframe"]
        start = cfg["run"]["start"]
        end = cfg["run"]["end"]

        df = fetch_rates_mt5(symbol, tf, start, end, utc=True)

        current_space = copy.deepcopy(cfg["search_space"])
        out_root = "out_wfo"
        os.makedirs(out_root, exist_ok=True)

        stage3_dir = None

        for i, base_stage in enumerate(BASE_STAGES):
            cfg["search_space"] = current_space
            stage_dir_base = os.path.join(out_root, f"stage_{i+1}_{base_stage['name']}")
            meta = {"stage": base_stage, "attempts": []}

            df_out = None
            new_space = None
            effective_cfg = None

            for attempt in range(4):
                stage_cfg = base_stage if attempt == 0 else auto_relax(base_stage, attempt - 1)
                print(
                    f"\n=== WFO Stage {i+1} | {stage_cfg['name']} | "
                    f"dd≤{stage_cfg['dd']} pf≥{stage_cfg['pf']} wm≥{stage_cfg['wm']} tr≥{stage_cfg['tr']} ==="
                )

                df_out, new_space, effective_cfg = run_stage(df, cfg, stage_cfg, n_trials=250)
                meta["attempts"].append({"stage_cfg": stage_cfg, "valid_trials": 0 if df_out is None else int(len(df_out))})

                if df_out is not None and len(df_out) > 0:
                    print(df_out.head(3).to_string(index=False))
                    break

            _write_stage(stage_dir_base, effective_cfg if effective_cfg else cfg, df_out, meta)

            if df_out is None or new_space is None:
                print("WFO pipeline stopped: no valid trials even after relax attempts.")
                return

            current_space = new_space

            if i == 2:
                stage3_dir = stage_dir_base

        print("\n=== WFO PIPELINE COMPLETE (Stages 1–3) ===")

        if stage3_dir is None:
            print("Stage 3 directory not found. Cannot run Stage 4.")
            return

        print("\n=== Stage 4: Monte Carlo Robustness (block bootstrap) ===")
        run_stage4_montecarlo(cfg, df, out_root=out_root, stage3_dir=stage3_dir)

        print("\n=== ALL DONE (WFO + MC) ===")
        print("Check out_wfo/stage_4_montecarlo/ for mc_summary.csv and final_config.json")

    main()