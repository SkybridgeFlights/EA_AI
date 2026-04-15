BASE_CONFIG = {
    # --- Core ---
    "TradeMode": 2,             # 0 AI, 1 HYB, 2 TECH
    "UseMA": True,
    "UseRSI": True,
    "UseAISignals": False,

    # --- RSI ---
    "InpRSI_Period": 11,
    "InpRSI_BuyMax": 61,
    "InpRSI_SellMin": 54,

    # --- ATR ---
    "InpATR_Period": 23,
    "InpMaxSpreadPts": 350,

    # --- Risk ---
    "InpRiskPct": 0.55,
    "MaxTradesPerDay": 9,
    "UseDailyLossStop": True,
    "DailyLossPct": 3.5,

    # --- BE & TS ---
    "UseTrailingStop": True,
    "TS_StartPts": 525,
    "TS_StepPts": 50,

    "UseBreakEven": True,
    "BE_TriggerPts": 170,
    "BE_OffsetPts": 0,

    "MinTradeGapSec": 330,

    # --- Disabled modules (optional; safe defaults) ---
    "UseFibonacciFilter": False,
    "UseRegimeDetector": False,
    "UseRiskGovernor": False,
    "UseMicrostructureFilters": False,
    "UseShadowSL": False,
    "UseLadderTP": False,
    "UsePyramiding": False,
    "Cloud_Enable": False,
}







