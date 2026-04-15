import os

from src.core.parameter_grid import GridGenerator
from src.core.set_builder import SetFileBuilder
from src.core.base_config import BASE_CONFIG
from src.core.ini_builder import INIBuilder
from src.core.mt5_executor import MT5Executor


MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

SYMBOL = "XAUUSDr"
PERIOD = "H1"
DEPOSIT = 10000

FROM_DATE = "2021.01.01"
TO_DATE = "2023.01.01"

SET_NAME = "auto_test.set"
TEMP_DIR = os.path.abspath("temp")
SET_PATH = os.path.join(TEMP_DIR, SET_NAME)

# IMPORTANT:
# Report in MT5 is a PREFIX (MT5 adds .htm/.html/.xml). Keep it as temp\report
REPORT_PREFIX = os.path.join(TEMP_DIR, "report")
INI_PATH = os.path.join(TEMP_DIR, "run.ini")


def main():
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 1) Build one config (sanity run)
    generator = GridGenerator()
    params = generator.generate()
    first = params[0]

    dynamic = {
        "InpMAfast": first.ma_fast,
        "InpMAslow": first.ma_slow,
        "InpATR_SL_Mult": first.atr_sl_mult,
        "InpRR": first.rr,
    }

    # 2) Write .set into temp first
    builder = SetFileBuilder(BASE_CONFIG)
    builder.save(dynamic, SET_PATH)

    # 3) Build run.ini
    ini = INIBuilder("EA V8.ex5")
    ini.build(
        SYMBOL,
        PERIOD,
        FROM_DATE,
        TO_DATE,
        DEPOSIT,
        SET_NAME,          # name-only in INI (auto_test.set)
        REPORT_PREFIX,     # prefix only: temp\report
        INI_PATH,
        model=0,
        optimization=0,
        leverage="1:100",
        currency="USD",
        shutdown_terminal=0,   # IMPORTANT: keep MT5 open (do not auto-close)
        replace_report=1,
    )

    # 4) Auto-fix BEFORE launching MT5:
    # - force ExpertParameters to name-only in INI
    # - copy .set to all candidate terminal data folders
    os.environ["MT5_INI"] = INI_PATH
    os.environ["MT5_PATH"] = MT5_PATH
    os.environ["MT5_DIAG_FIX"] = "1"
    from src.core import mt5_diag  # noqa
    mt5_diag.main()

    # 5) Run MT5 and wait professionally
    executor = MT5Executor(MT5_PATH)
    print("Running MT5...")
    executor.run(INI_PATH)

    # Wait up to 2 hours (adjust if needed)
    wr = executor.wait_for_report_professional(
        REPORT_PREFIX,
        timeout_sec=2 * 60 * 60,
        poll_sec=1.0,
        post_finish_grace_sec=3.0,
    )

    print("\n==============================")
    print("MT5 WAIT RESULT")
    print("==============================")
    print("Finished:", wr.finished)
    print("Finish reason:", wr.finish_reason)
    print("Log path:", wr.log_path)
    print("Report path:", wr.report_path)
    print("Final balance:", wr.final_balance)
    print("Test passed:", wr.test_passed)
    print("Summary JSON:", wr.summary_path)

    # If report is missing, this is still OK (we rely on log + summary JSON).
    if not wr.report_path:
        print("\nNOTE: Report file was not found. This does NOT block the pipeline.")
        print("We will use the log + result_summary.json as the official output for now.")


if __name__ == "__main__":
    main()