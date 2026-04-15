# -*- coding: utf-8 -*-ru_topn.py
"""
يجري Grid + Random ويولّد أفضل N نتائج (.json + .set).
الاعتماد: app/services/{backtest.py, calibrator.py, writer.py}
"""
from __future__ import annotations
import os, json, argparse, itertools, random
from typing import Dict, Any, Iterable, Tuple, List
from app.services.backtest import load_csv, BTParams, backtest
from app.services.writer   import save_set
from app.services.calibrator import objective  # نفس دالة التقييم

def _grid(values: Dict[str, Iterable[Any]]):
    keys=list(values.keys())
    for combo in itertools.product(*[values[k] for k in keys]):
        yield dict(zip(keys, combo))

def _random_space(space: Dict[str, Tuple[Any,Any,str]], n:int):
    for _ in range(n):
        cand={}
        for k,(a,b,t) in space.items():
            if t=="int":   cand[k]=random.randint(int(a), int(b))
            elif t=="float": cand[k]=round(random.uniform(float(a), float(b)),3)
            else: cand[k]=random.choice([a,b])
        yield cand

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/XAUUSD_H1.csv")
    ap.add_argument("--out", default="calibration_out")
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--random-trials", type=int, default=300)
    ap.add_argument("--dd-cap", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=42)
    args=ap.parse_args()
    random.seed(args.seed)
    os.makedirs(args.out, exist_ok=True)

    df=load_csv(args.csv)

    grid = {
        "ma_fast":[10,20,30],
        "ma_slow":[50,80,120],
        "atr_per":[14,21],
        "atr_mult":[1.6,2.0,2.4],
        "rr":[1.8,2.0,2.4],
        "rsi_per":[14],
        "rsi_buy_max":[55,60],
        "rsi_sell_min":[40,45],
    }
    rspace = {
        "ma_fast":(8,35,"int"),
        "ma_slow":(45,200,"int"),
        "atr_per":(10,28,"int"),
        "atr_mult":(1.2,3.0,"float"),
        "rr":(1.4,3.0,"float"),
        "rsi_per":(10,21,"int"),
        "rsi_buy_max":(50,65,"int"),
        "rsi_sell_min":(35,50,"int"),
        "max_spread_pts":(150,600,"int"),
        "spread_sl_factor":(1.5,3.5,"float"),
        "commission_per_lot":(4.0,10.0,"float"),
        "risk_pct":(0.2,1.2,"float"),
    }

    results: List[Dict[str,Any]] = []

    def run_params(params: Dict[str,Any]):
        p = BTParams(**{**vars(BTParams()), **params})
        res = backtest(df, p)
        if res.max_dd_pct > args.dd_cap: return
        sc = objective(res)
        results.append({
            "score": round(sc,3),
            "profit_pct": res.profit_pct,
            "max_dd_pct": res.max_dd_pct,
            "sharpe": res.sharpe,
            "trades": res.trades,
            "winrate": res.winrate,
            "avg_r": res.avg_r,
            "params": res.params
        })

    # Grid
    for g in _grid(grid): run_params(g)
    # Random
    for r in _random_space(rspace, args.random_trials): run_params(r)

    if not results: raise SystemExit("لا توجد نتائج ضمن قيود الـDD.")

    # sort & keep Top-N
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:args.topn]

    # write summary + sets
    with open(os.path.join(args.out,"top_results.json"),"w",encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    for i,item in enumerate(top, start=1):
        set_map = {
            # General
            "InpMagic": 20250918, "InpSymbol":"", "UseCurrentSymbol": True,
            # Signals
            "UseMA": True, "InpMAfast": item["params"]["ma_fast"], "InpMAslow": item["params"]["ma_slow"],
            "InpMA_Method": 1, "InpMA_Price": 0,
            "UseRSI": True, "InpRSI_Period": item["params"]["rsi_per"],
            "InpRSI_BuyMax": item["params"]["rsi_buy_max"], "InpRSI_SellMin": item["params"]["rsi_sell_min"],
            # ATR / SL / TP
            "InpATR_Period": item["params"]["atr_per"], "InpATR_SL_Mult": item["params"]["atr_mult"],
            "InpRR": item["params"]["rr"], "InpMaxSpreadPts": item["params"].get("max_spread_pts",350),
            # Broker adaptation
            "AutoConfig": True, "AC_LookbackDays": 90,
            "InpSpreadSLFactor": item["params"].get("spread_sl_factor",2.5),
            "InpCommissionPerLot": item["params"].get("commission_per_lot",7.0),
            # Risk
            "InpRiskPct": item["params"].get("risk_pct",0.5),
            "MaxOpenPerSymbol": 1, "MaxTradesPerDay": 10, "UseDailyLossStop": True, "DailyLossPct": 3.0,
            # BE/TS/PC
            "UseTrailingStop": True, "TS_StartPts": 300, "TS_StepPts": 100,
            "UseBreakEven": True, "BE_TriggerPts": 100, "BE_OffsetPts": 20,
            "UsePartialClose": False, "PC_TriggerPts": 400, "PC_CloseFrac": 0.5,
            # Session
            "UseSessionFilter": False, "Sess_GMT_Offset_Min": 0, "Sess_Start_H": 7, "Sess_End_H": 22,
            "TradeOnClosedBar": True, "MinTradeGapSec": 10, "DirInput": 0,
            # Calendar / EE / AI / Debug (قيَم افتراضية جيدة)
            "UseCalendarNews": True, "Cal_NoTradeBeforeMin": 5, "Cal_NoTradeAfterMin": 5, "Cal_MinImpact": 2, "Cal_Currencies": "",
            "UseEmergencyExit": True, "EE_NewsMinImpact": 3, "EE_NewsBeforeMin": 2, "EE_NewsAfterMin": 5,
            "EE_CloseOnOppositeAI": True, "EE_AI_MinConfidence": 0.80, "EE_ProtectInsteadOfClose": True,
            "EE_BE_OffsetPts": 25, "EE_TightTS_Start": 150, "EE_TightTS_Step": 50,
            "EE_PartialInsteadOfClose": True, "EE_PartialFrac": 0.50, "EE_MaxAdverseATR": 0.80,
            "EE_WaitFirstPulse": True, "EE_PulseSeconds": 60,
            "UseAISignals": False, "AI_MinConfidence": 0.70, "AI_RiskCapPct": 1.00, "AI_MaxHoldMinutes": 60,
            "AISignalFile":"ai_signals/xauusd_signal.ini","AI_ShadowMode": True,"AI_FreshSeconds": 90,"AI_CooldownMinutes": 5,
            "InpDebug": True, "InpDebugVerbose": True
        }
        save_set(set_map, os.path.join(args.out, f"top_{i:02d}.set"))

    print(f"تم إنشاء {len(top)} ملف .set + top_results.json داخل {args.out}")

if __name__=="__main__":
    main()






    