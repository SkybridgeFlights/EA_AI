# src/optimize_phaseA.py
from __future__ import annotations
import os
import pandas as pd
import optuna

from .objective import load_yaml, make_objective
from .data_mt5 import fetch_rates_mt5, save_parquet, load_parquet


def main():
    cfg = load_yaml("configs/phaseA_search.yaml")
    symbol = cfg["run"]["symbol"]
    tf = cfg["run"]["timeframe"]
    start = cfg["run"]["start"]
    end = cfg["run"]["end"]

    os.makedirs("data", exist_ok=True)
    data_path = f"data/{symbol}_{tf}_{start}_{end}.parquet".replace(":", "-")

    if os.path.exists(data_path):
        df = load_parquet(data_path)
    else:
        df = fetch_rates_mt5(symbol, tf, start, end, utc=cfg["run"].get("utc", True))
        save_parquet(df, data_path)

    objective_fn = make_objective(df, cfg)
    assert callable(objective_fn), f"make_objective() returned non-callable: {type(objective_fn)}"

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))

    n_trials = 400
    study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=True)

    best = sorted([t for t in study.trials if t.value is not None], key=lambda x: x.value, reverse=True)[:10]
    os.makedirs("out", exist_ok=True)

    rows = []
    for t in best:
        row = {"score": t.value, **t.params, **t.user_attrs}
        rows.append(row)

    out_df = pd.DataFrame(rows)
    out_df.to_csv("out/top10_phaseA.csv", index=False)
    print("Saved: out/top10_phaseA.csv")
    print(out_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()