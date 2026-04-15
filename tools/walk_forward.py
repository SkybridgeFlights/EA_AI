# -*- coding: utf-8 -*-
"""
walk_forward.py
- تقسيم زمني تلقائي (Train -> Forward) مع معايرة على Train واختبار أفضل N على Forward.
- يعتمد backtest/BTParams/load_csv مباشرة (بدون تعديل الإكسبرت).
- يخرج تقارير JSON/CSV وملفات .set لأفضل إعداد إجمالي + أفضل إعداد "مستقر" (robust).
"""
from __future__ import annotations
import os, json, argparse, random, math, csv
from typing import Dict, Any, Iterable, Tuple, List
import numpy as np
import pandas as pd

# استدعاء خدماتك الحالية
from app.services.backtest import load_csv, BTParams, backtest
from app.services.writer   import save_set

# ---------------- المساحات والهدف ----------------
def objective(res) -> float:
    # نفس منطق الدرجات (قابل للتعديل لاحقاً)
    sc = res.profit_pct - 0.8 * res.max_dd_pct + 5.0 * res.sharpe
    if res.trades < 40:
        sc -= (40 - res.trades) * 0.5
    return float(sc)

def random_space_draw(space: Dict[str, Tuple[Any, Any, str]], n: int) -> Iterable[Dict[str, Any]]:
    for _ in range(n):
        cand = {}
        for k, (a, b, t) in space.items():
            if t == "int":
                cand[k] = int(random.randint(int(a), int(b)))
            elif t == "float":
                cand[k] = round(random.uniform(float(a), float(b)), 3)
            else:
                # نوع تصنيفي/خياري بسيط
                cand[k] = random.choice([a, b])
        yield cand

def grid_enumerate(values: Dict[str, Iterable[Any]]) -> Iterable[Dict[str, Any]]:
    import itertools
    keys = list(values.keys())
    for combo in itertools.product(*[values[k] for k in keys]):
        yield dict(zip(keys, combo))

# نفس خريطة إخراج .set المستخدمة في calibrator
def params_to_set_map(best_params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        # General
        "InpMagic": 20250918,
        "InpSymbol": "",
        "UseCurrentSymbol": True,
        # Signals
        "UseMA": True,
        "InpMAfast": best_params["ma_fast"],
        "InpMAslow": best_params["ma_slow"],
        "InpMA_Method": 1,  # MODE_EMA
        "InpMA_Price": 0,   # PRICE_CLOSE
        "UseRSI": True,
        "InpRSI_Period": best_params["rsi_per"],
        "InpRSI_BuyMax": best_params["rsi_buy_max"],
        "InpRSI_SellMin": best_params["rsi_sell_min"],
        # ATR / SL / TP
        "InpATR_Period": best_params["atr_per"],
        "InpATR_SL_Mult": best_params["atr_mult"],
        "InpRR": best_params["rr"],
        "InpMaxSpreadPts": best_params["max_spread_pts"],
        # Broker adaptation
        "AutoConfig": True,
        "AC_LookbackDays": 90,
        "InpSpreadSLFactor": best_params["spread_sl_factor"],
        "InpCommissionPerLot": best_params["commission_per_lot"],
        # Risk
        "InpRiskPct": best_params["risk_pct"],
        "MaxOpenPerSymbol": 1,
        "MaxTradesPerDay": 10,
        "UseDailyLossStop": True,
        "DailyLossPct": 3.0,
        # BE/TS/PC
        "UseTrailingStop": True,
        "TS_StartPts": 300,
        "TS_StepPts": 100,
        "UseBreakEven": True,
        "BE_TriggerPts": 100,
        "BE_OffsetPts": 20,
        "UsePartialClose": False,
        "PC_TriggerPts": 400,
        "PC_CloseFrac": 0.5,
        # Session
        "UseSessionFilter": False,
        "Sess_GMT_Offset_Min": 0,
        "Sess_Start_H": 7,
        "Sess_End_H": 22,
        "TradeOnClosedBar": True,
        "MinTradeGapSec": 10,
        "DirInput": 0,
        # Calendar
        "UseCalendarNews": True,
        "Cal_NoTradeBeforeMin": 5,
        "Cal_NoTradeAfterMin": 5,
        "Cal_MinImpact": 2,
        "Cal_Currencies": "",
        # EE
        "UseEmergencyExit": True,
        "EE_NewsMinImpact": 3,
        "EE_NewsBeforeMin": 2,
        "EE_NewsAfterMin": 5,
        "EE_CloseOnOppositeAI": True,
        "EE_AI_MinConfidence": 0.80,
        "EE_ProtectInsteadOfClose": True,
        "EE_BE_OffsetPts": 25,
        "EE_TightTS_Start": 150,
        "EE_TightTS_Step": 50,
        "EE_PartialInsteadOfClose": True,
        "EE_PartialFrac": 0.50,
        "EE_MaxAdverseATR": 0.80,
        "EE_WaitFirstPulse": True,
        "EE_PulseSeconds": 60,
        # AI
        "UseAISignals": False,
        "AI_MinConfidence": 0.70,
        "AI_RiskCapPct": 1.00,
        "AI_MaxHoldMinutes": 60,
        "AISignalFile": "ai_signals/xauusd_signal.ini",
        "AI_ShadowMode": True,
        "AI_FreshSeconds": 90,
        "AI_CooldownMinutes": 5,
        # Debug
        "InpDebug": True,
        "InpDebugVerbose": True
    }

# ---------------- تقسيمات الوقت ----------------
def build_walk_forward_splits(df: pd.DataFrame, splits:int, train_months:int, fwd_months:int):
    """
    يعيد قائمة من العناصر: (train_mask, fwd_mask, meta_dict)
    """
    t = pd.to_datetime(df["Time"])
    start = t.min()
    # بداية أول نافذة تدريبية
    train_start = start
    out = []
    for k in range(splits):
        train_end   = train_start + pd.DateOffset(months=train_months)
        fwd_end     = train_end   + pd.DateOffset(months=fwd_months)

        train_mask = (t >= train_start) & (t < train_end)
        fwd_mask   = (t >= train_end)   & (t < fwd_end)

        # لو النافذة فقيرة بيانات نتوقف
        if train_mask.sum() < 300 or fwd_mask.sum() < 100:
            break

        out.append((
            train_mask.values,
            fwd_mask.values,
            dict(idx=k+1, train_start=str(train_start), train_end=str(train_end),
                 fwd_start=str(train_end), fwd_end=str(fwd_end))
        ))

        # تقدّم النافذة للأمام: ابدأ التدريب من نهاية الفوروارد السابقة
        train_start = train_start + pd.DateOffset(months=fwd_months)
    return out

# ---------------- تنفيذ نافذة واحدة ----------------
def evaluate_window(df: pd.DataFrame,
                    train_mask: np.ndarray,
                    fwd_mask: np.ndarray,
                    random_trials:int,
                    dd_cap: float,
                    topn: int) -> Dict[str,Any]:
    # مساحات البحث (مطابقة لما استخدمناه)
    grid = {
        "ma_fast": [10, 20, 30],
        "ma_slow": [50, 80, 120],
        "atr_per": [14, 21],
        "atr_mult": [1.6, 2.0, 2.4],
        "rr": [1.8, 2.0, 2.4],
        "rsi_per": [14],
        "rsi_buy_max": [55, 60],
        "rsi_sell_min": [40, 45],
    }
    rspace = {
        "ma_fast": (8, 35, "int"),
        "ma_slow": (45, 200, "int"),
        "atr_per": (10, 28, "int"),
        "atr_mult": (1.2, 3.0, "float"),
        "rr": (1.4, 3.0, "float"),
        "rsi_per": (10, 21, "int"),
        "rsi_buy_max": (50, 65, "int"),
        "rsi_sell_min": (35, 50, "int"),
        "max_spread_pts": (150, 600, "int"),
        "spread_sl_factor": (1.5, 3.5, "float"),
        "commission_per_lot": (4.0, 10.0, "float"),
        "risk_pct": (0.2, 1.2, "float"),
    }

    df_train = df.loc[train_mask].reset_index(drop=True)
    df_fwd   = df.loc[fwd_mask].reset_index(drop=True)

    # تقييم المرشحين على Train
    candidates: List[Tuple[float, Dict[str,Any], Any]] = []

    # Grid
    for params in grid_enumerate(grid):
        p = BTParams(**{**vars(BTParams()), **params})
        res = backtest(df_train, p)
        if res.max_dd_pct <= dd_cap:
            candidates.append((objective(res), params, res))

    # Random
    for params in random_space_draw(rspace, random_trials):
        p = BTParams(**{**vars(BTParams()), **params})
        res = backtest(df_train, p)
        if res.max_dd_pct <= dd_cap:
            candidates.append((objective(res), params, res))

    if not candidates:
        raise RuntimeError("لا توجد نتائج ضمن القيود في نافذة ما.")

    # أفضل N على Train
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:max(1, topn)]

    # اختبار أفضل N على Forward
    forward_rows = []
    for rank, (_, params, train_res) in enumerate(top, start=1):
        p = BTParams(**{**vars(BTParams()), **params})
        f_res = backtest(df_fwd, p)

        forward_rows.append({
            "rank_train": rank,
            "score_train": round(objective(train_res),3),
            "profit_train": train_res.profit_pct,
            "dd_train": train_res.max_dd_pct,
            "sharpe_train": train_res.sharpe,
            "trades_train": train_res.trades,

            "profit_fwd": f_res.profit_pct,
            "dd_fwd": f_res.max_dd_pct,
            "sharpe_fwd": f_res.sharpe,
            "trades_fwd": f_res.trades,

            "params": params
        })

    # اختيارين مفيدين: أفضل Forward بالربحية المركبة، وأفضل توازن (ربح -0.8DD + 5Sharpe)
    def score_forward_row(r):
        return r["profit_fwd"] - 0.8*r["dd_fwd"] + 5.0*r["sharpe_fwd"]

    best_fwd_by_score = max(forward_rows, key=score_forward_row)
    best_fwd_by_profit = max(forward_rows, key=lambda r: r["profit_fwd"])

    return {
        "top_forward": forward_rows,
        "best_fwd_by_score": best_fwd_by_score,
        "best_fwd_by_profit": best_fwd_by_profit
    }

# ---------------- تجميع النتائج عبر النوافذ ----------------
def summarize_all(windows_reports: List[Dict[str,Any]]) -> Dict[str,Any]:
    # نجمع جميع الصفوف (TopN عبر كل النوافذ)
    all_rows = []
    for wi, w in enumerate(windows_reports, start=1):
        for r in w["top_forward"]:
            row = dict(r)
            row["window"] = wi
            all_rows.append(row)

    # حساب متوسط الترتيب بالـForward لكل بارام
    # مفتاح التمييز: تجميد dict للبارامز لسلسلة JSON
    from collections import defaultdict
    group = defaultdict(list)
    for r in all_rows:
        key = json.dumps(r["params"], sort_keys=True)
        group[key].append(r)

    summary = []
    for key, rows in group.items():
        # رُتب forward داخل كل نافذة حسب الربحية مثلاً
        # لنحسب ترتيب كل صف داخل نافذته:
        # سنحتاج خريطة (window -> rows of that window) أولاً
        summary.append({
            "params_key": key,
            "params": rows[0]["params"],
            "avg_profit_fwd": float(np.mean([x["profit_fwd"] for x in rows])),
            "avg_dd_fwd": float(np.mean([x["dd_fwd"] for x in rows])),
            "avg_sharpe_fwd": float(np.mean([x["sharpe_fwd"] for x in rows])),
            "wins": sum(1 for x in rows if x["profit_fwd"]>0),
            "count": len(rows)
        })

    # أفضل إجمالي بالمعيار المركب
    def agg_score(s):
        return s["avg_profit_fwd"] - 0.8*s["avg_dd_fwd"] + 5.0*s["avg_sharpe_fwd"]

    best_overall = max(summary, key=agg_score)
    # “Robust” = أعلى متوسط ربح مع DD منخفض (أو أعلى wins/count)
    robust = max(summary, key=lambda s: (s["wins"]/max(1,s["count"]), s["avg_profit_fwd"] - 0.5*s["avg_dd_fwd"]))

    return {
        "all_rows": all_rows,
        "summary_by_params": summary,
        "best_overall": best_overall,
        "best_robust": robust
    }

# ---------------- حفظ CSV سريع ----------------
def write_csv(path: str, rows: List[Dict[str,Any]]):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # فرد params داخل الصف
    flat_rows=[]
    for r in rows:
        fr=dict(r)
        if "params" in fr and isinstance(fr["params"], dict):
            for k,v in fr["params"].items():
                fr[f"p_{k}"]=v
            del fr["params"]
        flat_rows.append(fr)
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=sorted(flat_rows[0].keys()))
        w.writeheader()
        for rr in flat_rows: w.writerow(rr)

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="مسار CSV مثل data/XAUUSD_H1.csv")
    ap.add_argument("--splits", type=int, default=6)
    ap.add_argument("--train-months", type=int, default=6)
    ap.add_argument("--forward-months", type=int, default=1)
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--random-trials", type=int, default=150)
    ap.add_argument("--dd-cap", type=float, default=60.0)
    ap.add_argument("--out", default="wf_out")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    df = load_csv(args.csv)
    splits = build_walk_forward_splits(df, args.splits, args.train_months, args.forward_months)
    if not splits:
        raise RuntimeError("لم أستطع إنشاء نوافذ كافية—تحقق من طول البيانات أو بارامترات التقسيم.")

    all_reports=[]
    for meta_idx, (tr_mask, fwd_mask, meta) in enumerate(splits, start=1):
        print(f"[WFA] Window {meta_idx}: Train={meta['train_start']}..{meta['train_end']} | Forward={meta['fwd_start']}..{meta['fwd_end']}")
        rep = evaluate_window(df, tr_mask, fwd_mask, args.random_trials, args.dd_cap, args.topn)
        # حفظ تقرير النافذة
        with open(os.path.join(args.out, f"window_{meta_idx}.json"),"w",encoding="utf-8") as f:
            json.dump({"meta":meta, **rep}, f, ensure_ascii=False, indent=2)
        write_csv(os.path.join(args.out, f"window_{meta_idx}_top.csv"), rep["top_forward"])
        all_reports.append(rep)

    # تلخيص إجمالي
    final = summarize_all(all_reports)
    with open(os.path.join(args.out,"summary.json"),"w",encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    write_csv(os.path.join(args.out,"all_rows.csv"), final["all_rows"])
    write_csv(os.path.join(args.out,"summary_by_params.csv"), final["summary_by_params"])

    # إنتاج .set لاثنين: الأفضل إجمالاً + الأكثر ثباتًا
    for tag, bundle in [("best_overall", final["best_overall"]), ("best_robust", final["best_robust"])]:
        set_map = params_to_set_map(bundle["params"])
        save_set(set_map, os.path.join(args.out, f"{tag}.set"))

    print("[WFA] Done.")
    print(f"- Summary: {os.path.join(args.out,'summary.json')}")
    print(f"- SET (overall): {os.path.join(args.out,'best_overall.set')}")
    print(f"- SET (robust) : {os.path.join(args.out,'best_robust.set')}")

if __name__=="__main__":
    main()









    