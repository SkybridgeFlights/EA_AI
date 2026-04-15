import os
import csv
import uuid
from typing import List, Dict, Any

from src.core.parameter_grid import GridGenerator
from src.core.set_builder import SetFileBuilder
from src.core.base_config import BASE_CONFIG
from src.core.ini_builder import INIBuilder
from src.core.mt5_executor import MT5Executor


MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
EXPERT = "EA V8.ex5"

SYMBOL = "XAUUSDr"
PERIOD = "H1"
DEPOSIT = 10000
LEVERAGE = "1:100"
CURRENCY = "USD"

SET_NAME = "auto_test.set"

TEMP_DIR = os.path.abspath("temp")
RUNS_DIR = os.path.join(TEMP_DIR, "wfo_runs")

FOLDS = [
    (("2021.01.01", "2021.12.31"), ("2022.01.01", "2022.06.30")),
    (("2021.07.01", "2022.06.30"), ("2022.07.01", "2022.12.31")),
    (("2022.01.01", "2022.12.31"), ("2023.01.01", "2023.06.30")),
]

MAX_TRAIN_TRIALS = 30
TOP_K = 5
TIMEOUT_SEC = 60 * 60  # 60 min


def score_from_summary(summary: Dict[str, Any]) -> float:
    fb = summary.get("final_balance")
    if fb is None:
        return -1e18
    return float(fb)


def run_one_backtest(
    executor: MT5Executor,
    ini_builder: INIBuilder,
    set_builder: SetFileBuilder,
    dynamic_params: Dict[str, Any],
    from_date: str,
    to_date: str,
    out_dir: str,
) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)

    set_path = os.path.join(out_dir, SET_NAME)
    set_builder.save(dynamic_params, set_path)

    report_prefix = os.path.join(out_dir, "report")
    ini_path = os.path.join(out_dir, "run.ini")

    ini_builder.build(
        SYMBOL,
        PERIOD,
        from_date,
        to_date,
        DEPOSIT,
        SET_NAME,
        report_prefix,
        ini_path,
        model=0,
        optimization=0,
        leverage=LEVERAGE,
        currency=CURRENCY,
        shutdown_terminal=0,
        replace_report=1,
    )

    os.environ["MT5_INI"] = ini_path
    os.environ["MT5_PATH"] = MT5_PATH
    os.environ["MT5_DIAG_FIX"] = "1"
    from src.core import mt5_diag  # noqa
    mt5_diag.main()

    executor.run(ini_path)
    wr = executor.wait_for_report_professional(
        report_prefix,
        ini_path=ini_path,  # <-- مهم: نخلي الـ executor يعتمد Report= الموجود في run.ini
        timeout_sec=TIMEOUT_SEC,
        poll_sec=1.0,
        post_finish_grace_sec=3.0,
        summary_json_path=os.path.join(out_dir, "result_summary.json"),
    )

    summary_path = wr.summary_path
    summary: Dict[str, Any] = {}
    if summary_path and os.path.exists(summary_path):
        import json
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)

    summary["from_date"] = from_date
    summary["to_date"] = to_date
    summary["out_dir"] = out_dir
    summary["params"] = dynamic_params
    return summary


def flatten_row(res: Dict[str, Any]) -> Dict[str, Any]:
    params = res.get("params", {}) or {}
    row = {
        "fold": res.get("fold"),
        "phase": res.get("phase"),
        "trial": res.get("trial"),
        "from_date": res.get("from_date"),
        "to_date": res.get("to_date"),
        "finished": res.get("finished"),
        "finish_reason": res.get("finish_reason"),
        "test_passed": res.get("test_passed"),
        "final_balance": res.get("final_balance"),
        "score": res.get("score"),
        "out_dir": res.get("out_dir"),
        "report_path": res.get("report_path"),
        "log_path": res.get("log_path"),
        "picked_from_train_trial": res.get("picked_from_train_trial"),
        "train_score": res.get("train_score"),
    }
    for k, v in params.items():
        row[k] = v
    return row


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    keys = sorted({k for r in rows for k in r.keys()})
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    os.makedirs(RUNS_DIR, exist_ok=True)

    grid = GridGenerator().generate()
    ini_builder = INIBuilder(EXPERT)
    set_builder = SetFileBuilder(BASE_CONFIG)
    executor = MT5Executor(MT5_PATH)

    all_rows: List[Dict[str, Any]] = []
    run_id = uuid.uuid4().hex[:10]
    master_csv = os.path.join(RUNS_DIR, f"wfo_master_{run_id}.csv")

    for fold_idx, (train, test) in enumerate(FOLDS, start=1):
        train_from, train_to = train
        test_from, test_to = test

        fold_dir = os.path.join(RUNS_DIR, f"fold_{fold_idx}_{run_id}")
        os.makedirs(fold_dir, exist_ok=True)

        train_results: List[Dict[str, Any]] = []
        for i, p in enumerate(grid[:MAX_TRAIN_TRIALS], start=1):
            dyn = {
                "InpMAfast": p.ma_fast,
                "InpMAslow": p.ma_slow,
                "InpATR_SL_Mult": p.atr_sl_mult,
                "InpRR": p.rr,
            }

            trial_dir = os.path.join(fold_dir, f"train_{i:03d}")
            res = run_one_backtest(
                executor, ini_builder, set_builder,
                dyn, train_from, train_to,
                trial_dir
            )
            res["fold"] = fold_idx
            res["phase"] = "train"
            res["trial"] = i
            res["score"] = score_from_summary(res)
            train_results.append(res)
            all_rows.append(flatten_row(res))

        train_sorted = sorted(train_results, key=lambda r: r.get("score", -1e18), reverse=True)
        top = train_sorted[:TOP_K]

        for j, best in enumerate(top, start=1):
            dyn = best["params"]
            test_dir = os.path.join(fold_dir, f"test_top_{j:02d}")
            res = run_one_backtest(
                executor, ini_builder, set_builder,
                dyn, test_from, test_to,
                test_dir
            )
            res["fold"] = fold_idx
            res["phase"] = "test"
            res["trial"] = j
            res["picked_from_train_trial"] = best.get("trial")
            res["train_score"] = best.get("score")
            res["score"] = score_from_summary(res)
            all_rows.append(flatten_row(res))

        fold_csv = os.path.join(fold_dir, f"fold_{fold_idx}_results.csv")
        write_csv(fold_csv, all_rows)

    write_csv(master_csv, all_rows)
    print("WFO done.")
    print("Master CSV:", master_csv)
    print("Runs dir:", RUNS_DIR)


if __name__ == "__main__":
    main()