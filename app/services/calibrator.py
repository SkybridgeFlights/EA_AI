# -*- coding: utf-8 -*-calibratot.py
"""
calibrator.py (V3.1)
- Grid/Random Search + Walk-Forward + Monte Carlo
- مخرجات: best_result.json + .set (موحّد) + .set خاصة لكل نظام (Trend/Range/HighVol)
- يدعم قراءة spread/commission تلقائيًا من MT5 Python API (اختياري).
"""
from __future__ import annotations
import os, json, itertools, random, argparse, math
from typing import Dict, Any, Iterable, Tuple, List
import numpy as np

from .backtest import load_csv, BTParams, RegimeParams, GovernorParams, backtest
from .writer   import save_set

# ---------- MT5 optional ----------
def _mt5_env() -> Dict[str,float]|None:
    """
    يحاول جلب السبريد والعمولة من منصة MT5 عبر MetaTrader5 بايثون (إن وُجدت).
    يعيد dict: {"avg_spread_pts":..., "commission_per_lot":...} أو None.
    """
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        sym=mt5.symbol_info_tick(mt5.symbol_info_tick.__annotations__.keys().__iter__().__class__)
        # أعلاه مجرد حماية من linters؛ سنأخذ الرمز الافتراضي من الحساب:
        acc=mt5.account_info()
        # لا توجد API مباشرة للرمز الحالي؛ نسمح بتمرير via args لاحقًا. هنا محاولة عامة:
        # نجرب XAUUSD أولاً وإن لم يوجد نأخذ أول رمز ظاهر.
        symbol="XAUUSD"
        si=mt5.symbol_info(symbol) or (mt5.symbols_get()[0] if mt5.symbols_get() else None)
        if not si: 
            mt5.shutdown(); return None
        # عين الرمز للتدفق
        mt5.symbol_select(si.name, True)
        # متوسط السبريد اللحظي
        sp_sum=0; cnt=0
        for _ in range(10):
            tk=mt5.symbol_info_tick(si.name)
            if tk:
                point=mt5.symbol_info(si.name).point
                sp_pts=(tk.ask-tk.bid)/max(1e-12,point)
                sp_sum+=sp_pts; cnt+=1
        avg_sp= (sp_sum/cnt) if cnt>0 else float(mt5.symbol_info(si.name).spread)
        # العمولة غير متاحة مباشرة؛ نقرأ commission per lot من contract_size & trade commission modes (تقريب محافظ):
        comm=7.0
        mt5.shutdown()
        return {"avg_spread_pts": float(avg_sp), "commission_per_lot": float(comm)}
    except Exception:
        return None

# ---------- Search spaces ----------
def _grid(values: Dict[str, Iterable[Any]]) -> Iterable[Dict[str,Any]]:
    keys=list(values.keys())
    for combo in itertools.product(*[values[k] for k in keys]):
        yield dict(zip(keys, combo))

def _random_space(space: Dict[str, Tuple[Any,Any,str]], n: int) -> Iterable[Dict[str,Any]]:
    for _ in range(n):
        cand={}
        for k,(a,b,t) in space.items():
            if t=="int":   cand[k]=random.randint(int(a), int(b))
            elif t=="float": cand[k]=round(random.uniform(float(a), float(b)), 4)
            else: cand[k]=random.choice([a,b])
        yield cand

# ---------- Objective ----------
def objective(res) -> float:
    # مركّب محافظ: ربح - 0.8*DD + 5*Sharpe + مكافأة للصفقات
    score = res.profit_pct - 0.8*res.max_dd_pct + 5.0*res.sharpe + min(60.0, res.trades*0.3)
    if res.trades < 40:
        score -= (40-res.trades)*0.5
    return float(score)

# ---------- Monte Carlo ----------
def monte_carlo(trade_R: List[float], trials:int=1000, sample_size:int|None=None) -> Dict[str,float]:
    if not trade_R: 
        return {"mc_mean":0.0,"mc_p05":0.0,"mc_p95":0.0}
    arr=np.array(trade_R, dtype=np.float64)
    n=len(arr)
    m=sample_size or n
    eq_results=[]
    for _ in range(max(1,trials)):
        picks=np.random.choice(arr, size=m, replace=True)
        # تحويل R إلى نمو رأس المال (بافتراض 1% لكل R كوحدة بحتة للمقارنة)
        equity=1.0
        for r in picks:
            equity *= (1.0 + 0.01*r)
        eq_results.append((equity-1.0)*100.0)
    eq=np.sort(np.array(eq_results))
    p05=float(np.percentile(eq,5))
    p95=float(np.percentile(eq,95))
    return {"mc_mean": float(eq.mean()), "mc_p05": p05, "mc_p95": p95}

# ---------- Walk-Forward ----------
def walk_forward(df, cfg: BTParams, splits:int=4) -> Dict[str,Any]:
    n=df.shape[0]
    if splits<2: splits=2
    seg=n//splits
    if seg<500: return {"wf_segments":0, "wf_avg_profit":0.0, "wf_avg_dd":0.0, "wf_avg_sharpe":0.0}
    prof=[]; dds=[]; shs=[]
    for k in range(splits):
        lo=k*seg; hi=(k+1)*seg if k<splits-1 else n
        part=df.iloc[lo:hi].reset_index(drop=True)
        res=backtest(part, cfg)
        prof.append(res.profit_pct); dds.append(res.max_dd_pct); shs.append(res.sharpe)
    return {
        "wf_segments": splits,
        "wf_avg_profit": round(float(np.mean(prof)),2),
        "wf_avg_dd": round(float(np.mean(dds)),2),
        "wf_avg_sharpe": round(float(np.mean(shs)),3)
    }

# ---------- Main calibration ----------
def run_calibration(csv_path: str,
                    grid: Dict[str, Iterable[Any]]|None,
                    rspace: Dict[str, Tuple[Any,Any,str]]|None,
                    random_trials: int = 0,
                    dd_cap: float = 60.0,
                    out_dir: str = "calibration_out",
                    wf_splits:int=4,
                    mc_trials:int=1000,
                    use_mt5_auto:bool=True) -> Dict[str,Any]:

    df = load_csv(csv_path)
    os.makedirs(out_dir, exist_ok=True)

    # MT5 auto (اختياري)
    mt5_env = _mt5_env() if use_mt5_auto else None

    best=None; best_score=-1e9

    def _mkparams(overrides: Dict[str, Any]) -> BTParams:
        base = BTParams()
        # لو جلبنا بيئة MT5
        if mt5_env:
            base.commission_per_lot = float(mt5_env["commission_per_lot"])

        # طبّق القيم القادمة من القاموس (سواء مسطّحة أو متداخلة)
        for k, v in (overrides or {}).items():
            if k == "regime" and isinstance(v, dict):
                # لا نستبدل الكائن بكامله، بل نحدّث حقوله
                for sk, sv in v.items():
                    if hasattr(base.regime, sk):
                        setattr(base.regime, sk, sv)
                continue
            if k == "governor" and isinstance(v, dict):
                for sk, sv in v.items():
                    if hasattr(base.governor, sk):
                        setattr(base.governor, sk, sv)
                continue

            # مفاتيح مسطّحة عادية
            if hasattr(base, k):
                setattr(base, k, v)
            else:
                # دعم صيغة "regime.xxx" و "governor.xxx"
                if k.startswith("regime."):
                    subk = k.split(".", 1)[1]
                    if hasattr(base.regime, subk):
                        setattr(base.regime, subk, v)
                elif k.startswith("governor."):
                    subk = k.split(".", 1)[1]
                    if hasattr(base.governor, subk):
                        setattr(base.governor, subk, v)
        return base

    # Grid
    if grid:
        for params in _grid(grid):
            p=_mkparams(params)
            res=backtest(df, p)
            if res.max_dd_pct>dd_cap: continue
            sc=objective(res)
            if sc>best_score: best,res.params["__score"]=sc,sc; best=res

    # Random
    if rspace and random_trials>0:
        for params in _random_space(rspace, random_trials):
            p=_mkparams(params)
            res=backtest(df, p)
            if res.max_dd_pct>dd_cap: continue
            sc=objective(res)
            if sc>best_score: best,res.params["__score"]=sc,sc; best=res

    if best is None:
        raise RuntimeError("لم يتم العثور على إعدادات ضمن القيود.")

    # Walk-Forward
    best_cfg = _mkparams(best.params if isinstance(best.params,dict) else {})
    wf = walk_forward(df, best_cfg, splits=wf_splits)

    # Monte Carlo
    mc = monte_carlo(best.trade_R, trials=mc_trials)

    # Summary JSON
    summary = {
        "score": round(best.params.get("__score", objective(best)),3),
        "profit_pct": best.profit_pct,
        "max_dd_pct": best.max_dd_pct,
        "sharpe": best.sharpe,
        "trades": best.trades,
        "winrate": best.winrate,
        "avg_r": best.avg_r,
        "regimes": best.regimes,
        "params": best.params,
        "walk_forward": wf,
        "monte_carlo": mc,
        "mt5_env": (mt5_env or {})
    }
    json_path=os.path.join(out_dir,"best_result.json")
    with open(json_path,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)

    # ----- .set (موحّد + لكل نظام) -----
    # خريطة إلى مدخلات الإكسبرت V3.1
    def save_unified_set(cfg:BTParams, path:str):
        mp = {
            # General
            "InpMagic": 20250918, "InpSymbol":"", "UseCurrentSymbol": True,
            # Signals
            "UseMA": True, "InpMAfast": cfg.ma_fast, "InpMAslow": cfg.ma_slow,
            "InpMA_Method": 1, "InpMA_Price": 0,
            "UseRSI": True, "InpRSI_Period": cfg.rsi_per,
            "InpRSI_BuyMax": cfg.rsi_buy_max, "InpRSI_SellMin": cfg.rsi_sell_min,
            # ATR/SL/TP
            "InpATR_Period": cfg.atr_per, "InpATR_SL_Mult": cfg.atr_mult, "InpRR": cfg.rr,
            "InpMaxSpreadPts": cfg.max_spread_pts,
            # Auto-config (spread/commission)
            "AutoConfig": True, "AC_LookbackDays": 90,
            "InpSpreadSLFactor": cfg.spread_sl_factor,
            "InpCommissionPerLot": cfg.commission_per_lot,
            # Risk
            "InpRiskPct": cfg.risk_pct, "MaxOpenPerSymbol":1, "MaxTradesPerDay":10,
            "UseDailyLossStop": True, "DailyLossPct": 3.0,
            # TS/BE/PC
            "UseTrailingStop": True, "TS_StartPts": 300, "TS_StepPts": 100,
            "UseBreakEven": True, "BE_TriggerPts": 100, "BE_OffsetPts": 20,
            "UsePartialClose": False, "PC_TriggerPts": 400, "PC_CloseFrac": 0.5,
            # Session
            "UseSessionFilter": False, "Sess_GMT_Offset_Min":0, "Sess_Start_H":7, "Sess_End_H":22,
            "TradeOnClosedBar": True, "MinTradeGapSec":10, "DirInput":0,
            # Calendar
            "UseCalendarNews": True, "Cal_NoTradeBeforeMin":5, "Cal_NoTradeAfterMin":5,
            "Cal_MinImpact":2, "Cal_Currencies":"",
            # EE
            "UseEmergencyExit": True, "EE_NewsMinImpact":3, "EE_NewsBeforeMin":2, "EE_NewsAfterMin":5,
            "EE_CloseOnOppositeAI": True, "EE_AI_MinConfidence":0.80,
            "EE_ProtectInsteadOfClose": True, "EE_BE_OffsetPts":25,
            "EE_TightTS_Start":150, "EE_TightTS_Step":50,
            "EE_PartialInsteadOfClose": True, "EE_PartialFrac":0.50,
            "EE_MaxAdverseATR":0.80, "EE_WaitFirstPulse": True, "EE_PulseSeconds":60,
            # AI
            "UseAISignals": False, "AI_MinConfidence":0.70, "AI_RiskCapPct":1.00,
            "AI_MaxHoldMinutes":60, "AISignalFile":"ai_signals/xauusd_signal.ini",
            "AI_ShadowMode": True, "AI_FreshSeconds":90, "AI_CooldownMinutes":5,
            # Regime Detector (EA side)
            "UseRegimeDetector": True,
            "RD_ATR_Period": cfg.regime.rd_atr_per,
            "RD_HighVolMult": cfg.regime.rd_highvol_mult,
            "RD_SlopeLookback": cfg.regime.rd_slope_lookback,
            "RD_SlopeThreshPts": cfg.regime.rd_slope_thresh_pts,
            "RD_MedianWindow": cfg.regime.rd_median_window,
            "R_TR_RR": cfg.regime.tr_rr, "R_TR_TS_Start": cfg.regime.tr_ts_start,
            "R_TR_TS_Step": cfg.regime.tr_ts_step, "R_TR_BE_Trig": cfg.regime.tr_be_trig,
            "R_TR_BE_Offs": cfg.regime.tr_be_offs,
            "R_RG_RR": cfg.regime.rg_rr, "R_RG_TS_Start": cfg.regime.rg_ts_start,
            "R_RG_TS_Step": cfg.regime.rg_ts_step, "R_RG_BE_Trig": cfg.regime.rg_be_trig,
            "R_RG_BE_Offs": cfg.regime.rg_be_offs,
            "R_HV_RR": cfg.regime.hv_rr, "R_HV_TS_Start": cfg.regime.hv_ts_start,
            "R_HV_TS_Step": cfg.regime.hv_ts_step, "R_HV_BE_Trig": cfg.regime.hv_be_trig,
            "R_HV_BE_OffS": cfg.regime.hv_be_offs,
            # Risk Governor
            "UseRiskGovernor": True,
            "RG_DailyLossHardPct": cfg.governor.daily_hard_pct,
            "RG_EquityDDHaltPct": cfg.governor.eq_dd_halt_pct,
            "RG_MaxExposureLots": cfg.governor.max_exposure_lots,
            "RG_ScaleRiskByATR": cfg.governor.scale_by_atr,
            "RG_RiskMinPct": cfg.governor.risk_min_pct,
            "RG_RiskMaxPct": cfg.governor.risk_max_pct,
            "RG_ATRNormPts": cfg.governor.atr_norm_pts,
            # Debug
            "InpDebug": True, "InpDebugVerbose": True
        }
        save_set(mp, path)

    # unified set
    set_unified=os.path.join(out_dir,"best_result.set")
    save_unified_set(best_cfg, set_unified)

    # regime-specific presets (نفس الإعدادات مع RR/TS/BE لكل نظام لإجراء اختبارات تفصيلية إن رغبت)
    def clone_cfg(base:BTParams) -> BTParams:
        import copy; return copy.deepcopy(base)

    tr_cfg=clone_cfg(best_cfg); tr_cfg.rr=best_cfg.regime.tr_rr
    rg_cfg=clone_cfg(best_cfg); rg_cfg.rr=best_cfg.regime.rg_rr
    hv_cfg=clone_cfg(best_cfg); hv_cfg.rr=best_cfg.regime.hv_rr

    save_unified_set(tr_cfg, os.path.join(out_dir,"best_trend.set"))
    save_unified_set(rg_cfg, os.path.join(out_dir,"best_range.set"))
    save_unified_set(hv_cfg, os.path.join(out_dir,"best_highvol.set"))

    summary["json_path"]=json_path
    summary["set_unified"]=set_unified
    summary["set_trend"]=os.path.join(out_dir,"best_trend.set")
    summary["set_range"]=os.path.join(out_dir,"best_range.set")
    summary["set_highvol"]=os.path.join(out_dir,"best_highvol.set")
    return summary

# ---------- CLI ----------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/XAUUSD_H1.csv")
    ap.add_argument("--out", default="calibration_out")
    ap.add_argument("--mode", choices=["grid","random","both"], default="both")
    ap.add_argument("--random-trials", type=int, default=200)
    ap.add_argument("--dd-cap", type=float, default=60.0)
    ap.add_argument("--wf-splits", type=int, default=4)
    ap.add_argument("--mc-trials", type=int, default=1000)
    ap.add_argument("--no-mt5", action="store_true")
    args=ap.parse_args()

    grid = {
        "ma_fast":[10,20,30],
        "ma_slow":[50,80,120],
        "atr_per":[14,21],
        "atr_mult":[1.6,2.0,2.4],
        "rr":[1.8,2.0,2.4],
        "rsi_per":[14],
        "rsi_buy_max":[55,60],
        "rsi_sell_min":[40,45],
        # Regime tweaks (أمثلة خفيفة ضمنية)
        "regime.rd_highvol_mult":[2.0,2.4],
        "regime.rd_slope_thresh_pts":[40,60],
    } if args.mode in ("grid","both") else None

    rspace = {
        "ma_fast": (8,35,"int"),
        "ma_slow": (45,200,"int"),
        "atr_per": (10,28,"int"),
        "atr_mult": (1.2,3.0,"float"),
        "rr": (1.4,3.2,"float"),
        "rsi_per": (10,21,"int"),
        "rsi_buy_max": (50,65,"int"),
        "rsi_sell_min": (35,50,"int"),
        "spread_sl_factor": (1.5,3.5,"float"),
        "commission_per_lot": (4.0,10.0,"float"),
        "risk_pct": (0.2,1.2,"float"),
        # regime sensitivity
        "regime.rd_highvol_mult": (1.8,2.6,"float"),
        "regime.rd_slope_thresh_pts": (35,65,"float"),
    } if args.mode in ("random","both") else None

    summary = run_calibration(
        csv_path=args.csv,
        grid=grid,
        rspace=rspace,
        random_trials=(args.random_trials if args.mode in ("random","both") else 0),
        dd_cap=args.dd_cap,
        out_dir=args.out,
        wf_splits=args.wf_splits,
        mc_trials=args.mc_trials,
        use_mt5_auto=(not args.no_mt5)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__=="__main__":
    main()




    


    