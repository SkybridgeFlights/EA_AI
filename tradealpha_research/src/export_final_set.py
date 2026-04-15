from __future__ import annotations
import json
import os

FINAL_JSON = "out_wfo/stage_4_montecarlo/final_config.json"
OUT_SET = "out_wfo/stage_4_montecarlo/final_config.set"

# Map Python param names to MT5 input names (same here)
# Keep only params that exist in your optimization space.
KEEP_KEYS = [
    "InpMAfast","InpMAslow","InpRSI_Period","InpRSI_BuyMax","InpRSI_SellMin",
    "InpATR_Period","InpATR_SL_Mult","InpRR","InpMaxSpreadPts",
    "InpRiskPct","MaxTradesPerDay","DailyLossPct",
    "UseTrailingStop","TS_StartPts","TS_StepPts",
    "UseBreakEven","BE_TriggerPts","BE_OffsetPts","MinTradeGapSec"
]

def mt5_bool(v: bool) -> str:
    return "true" if bool(v) else "false"

def main():
    with open(FINAL_JSON, "r", encoding="utf-8") as f:
        j = json.load(f)

    params = j.get("params", {})
    lines = []

    for k in KEEP_KEYS:
        if k not in params:
            continue
        v = params[k]
        if isinstance(v, bool):
            vv = mt5_bool(v)
        else:
            vv = str(v)
        # MT5 .set format: Key=Value
        lines.append(f"{k}={vv}")

    os.makedirs(os.path.dirname(OUT_SET), exist_ok=True)
    with open(OUT_SET, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("Saved:", OUT_SET)

if __name__ == "__main__":
    main()