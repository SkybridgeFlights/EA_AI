# src/pipeline_auto.py
from __future__ import annotations

import os
import copy
import math
import yaml
import pandas as pd
import numpy as np
import optuna

from .objective import make_objective, load_yaml
from .data_mt5 import fetch_rates_mt5


# مراحل أساسية (يمكن تعديلها لاحقًا)
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
            # fallback: widen one step around center
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

    # bool
    return float(lo), float(hi)


def narrow_search_space(df_results: pd.DataFrame, space: dict, keep_ratio: float = 0.2) -> dict:
    """
    يضيق النطاقات حول أفضل keep_ratio من النتائج،
    مع محاذاة الحدود على step لتجنب تحذيرات Optuna.
    """
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

        # widen a bit around observed best region
        margin = (hi - lo) * 0.25
        lo = max(base_lo, lo - margin)
        hi = min(base_hi, hi + margin)

        lo, hi = _align_bounds_to_step(spec, lo, hi)

        new_space[name]["low"] = lo if t == "float" else int(lo)
        new_space[name]["high"] = hi if t == "float" else int(hi)

    return new_space


def _write_stage(cfg: dict, stage_dir: str, df_out: pd.DataFrame | None, meta: dict):
    os.makedirs(stage_dir, exist_ok=True)
    with open(os.path.join(stage_dir, "stage_meta.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)

    with open(os.path.join(stage_dir, "effective_config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    if df_out is not None and len(df_out) > 0:
        df_out.to_csv(os.path.join(stage_dir, "results.csv"), index=False)


def run_stage(df: pd.DataFrame, cfg: dict, stage_cfg: dict, stage_index: int, n_trials: int = 300):
    """
    يرجع (df_out, new_space). إذا لا يوجد valid trials يرجع (None, None)
    """
    cfg = copy.deepcopy(cfg)
    cfg["constraints"]["max_dd_pct"] = float(stage_cfg["dd"])
    cfg["constraints"]["min_pf"] = float(stage_cfg["pf"])
    cfg["constraints"]["worst_month_pct"] = float(stage_cfg["wm"])
    cfg["constraints"]["min_trades"] = int(stage_cfg.get("tr", cfg["constraints"].get("min_trades", 200)))

    objective = make_objective(df, cfg)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=int(n_trials), show_progress_bar=True)

    trials = [t for t in study.trials if t.value is not None]
    if not trials:
        return None, None, cfg

    rows = []
    for t in trials:
        rows.append({"score": t.value, **t.params, **t.user_attrs})

    df_out = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    new_space = narrow_search_space(df_out, cfg["search_space"], keep_ratio=0.2)
    return df_out, new_space, cfg


def auto_relax(stage: dict, attempt: int) -> dict:
    """
    إذا Stage فشل (0 valid) نخفف القيود تدريجيًا.
    attempt=0 أول تخفيف، ثم أقوى…
    """
    s = dict(stage)
    # Relax schedule
    # dd: +1.5 كل محاولة، pf: -0.03، worst month: -1.0 (أقل صرامة)، trades: -20
    s["dd"] = float(s["dd"]) + 1.5 * (attempt + 1)
    s["pf"] = max(1.05, float(s["pf"]) - 0.03 * (attempt + 1))
    s["wm"] = float(s["wm"]) - 1.0 * (attempt + 1)
    s["tr"] = max(60, int(s.get("tr", 200)) - 20 * (attempt + 1))
    s["name"] = f"{s['name']}_relax{attempt+1}"
    return s


def main():
    cfg = load_yaml("configs/phaseA_search.yaml")

    symbol = cfg["run"]["symbol"]
    tf = cfg["run"]["timeframe"]
    start = cfg["run"]["start"]
    end = cfg["run"]["end"]

    df = fetch_rates_mt5(symbol, tf, start, end, utc=True)

    current_space = copy.deepcopy(cfg["search_space"])
    stages = copy.deepcopy(BASE_STAGES)

    os.makedirs("out", exist_ok=True)

    for i, base_stage in enumerate(stages):
        cfg["search_space"] = current_space

        stage_dir = f"out/stage_{i+1}_{base_stage['name']}"
        meta = {"stage": base_stage, "attempts": []}

        # نحاول stage حتى 3 مرات مع relax تلقائي إذا فشل
        df_out = None
        new_space = None
        effective_cfg = None

        for attempt in range(4):  # 0..3
            stage_cfg = base_stage if attempt == 0 else auto_relax(base_stage, attempt - 1)
            print(f"\n=== Stage {i+1} | {stage_cfg['name']} | dd≤{stage_cfg['dd']} pf≥{stage_cfg['pf']} wm≥{stage_cfg['wm']} tr≥{stage_cfg['tr']} ===")

            df_out, new_space, effective_cfg = run_stage(df, cfg, stage_cfg, i, n_trials=300)

            meta["attempts"].append({"stage_cfg": stage_cfg, "valid_trials": 0 if df_out is None else int(len(df_out))})

            if df_out is not None and len(df_out) > 0:
                print(df_out.head(3).to_string(index=False))
                break

        _write_stage(effective_cfg if effective_cfg else cfg, stage_dir, df_out, meta)

        if df_out is None or new_space is None:
            print("Pipeline stopped: no valid trials even after relax attempts.")
            return

        current_space = new_space

    print("\n=== PIPELINE COMPLETE ===")
    print("Check out/ for staged results and configs.")


if __name__ == "__main__":
    main()