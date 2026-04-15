# tools/bt_ea_like.py
# باك-تيست سريع على CSV بصيغة: Time,Open,High,Low,Close
# يحاكي: EMA كروس + فلتر RSI + SL/TP بالـ ATR (بنَفَس إعدادات الإكسبرت)

import json, math, argparse
from pathlib import Path
import pandas as pd
import numpy as np

# ===== مؤشرات خفيفة =====
def ema(x, n): return x.ewm(span=n, adjust=False).mean()
def rsi(close, p=14):
    d = close.diff()
    up = d.clip(lower=0.0); dn = (-d.clip(upper=0.0))
    rg = up.ewm(alpha=1/p, adjust=False).mean()
    rl = dn.ewm(alpha=1/p, adjust=False).mean()
    rs = rg/(rl+1e-12); return 100 - (100/(1+rs))
def atr(high, low, close, p=14):
    prev = close.shift(1)
    tr = pd.concat([(high-low).abs(), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, adjust=False).mean()

def run_bt(df, params):
    # إعدادات
    ma_fast     = int(params.get("MAfast", 20))
    ma_slow     = int(params.get("MAslow", 50))
    rsi_per     = int(params.get("RSI_Period", 14))
    rsi_buy_max = float(params.get("RSI_BuyMax", 60))
    rsi_sell_min= float(params.get("RSI_SellMin", 40))
    atr_p       = int(params.get("ATR_Period", 14))
    atr_mult    = float(params.get("ATR_SL_Mult", 2.0))
    rr          = float(params.get("RR", 2.0))
    be_trig     = int(params.get("BE_Trig", 100))   # نقاط
    be_offs     = int(params.get("BE_Offs", 20))    # نقاط
    ts_start    = int(params.get("TS_Start", 300))  # نقاط
    ts_step     = int(params.get("TS_Step", 100))   # نقاط
    point       = float(params.get("Point", 0.1))   # XAUUSD غالبًا 0.01 أو 0.1 حسب الوسيط
    tick_value  = float(params.get("TickValue", 1.0))
    tick_size   = float(params.get("TickSize", 0.1))

    close = pd.to_numeric(df["Close"], errors="coerce")
    high  = pd.to_numeric(df["High"], errors="coerce")
    low   = pd.to_numeric(df["Low"],  errors="coerce")
    openp = pd.to_numeric(df["Open"], errors="coerce")

    ema_f = ema(close, ma_fast)
    ema_s = ema(close, ma_slow)
    rsi_v = rsi(close, rsi_per)
    atr_v = atr(high, low, close, atr_p)

    # إشارات كروس (على الشمعة المغلقة)
    f_prev = ema_f.shift(1); s_prev = ema_s.shift(1)
    buy_sig  = (f_prev <= s_prev) & (ema_f > ema_s) & (rsi_v <= rsi_buy_max)
    sell_sig = (f_prev >= s_prev) & (ema_f < ema_s) & (rsi_v >= rsi_sell_min)

    trades = []
    pos = None  # {dir:+1/-1, open_px, sl_pts, tp_pts, sl, tp, be_done, last_sl}
    for i in range(len(df)):
        px_open = openp.iloc[i]
        px_high = high.iloc[i]
        px_low  = low.iloc[i]
        px_close= close.iloc[i]
        at = atr_v.iloc[i]/point if not math.isnan(atr_v.iloc[i]) else 0.0

        # إدارة مركز مفتوح
        if pos is not None:
            # ربح/خسارة بالنقاط من سعر الدخول إلى آخر سعر (لتفعيل BE/TS فقط)
            gain_pts = (px_close - pos["open_px"])/point if pos["dir"]>0 else (pos["open_px"] - px_close)/point

            # BreakEven
            if not pos["be_done"] and gain_pts >= be_trig:
                be_px = pos["open_px"] + be_offs*point if pos["dir"]>0 else pos["open_px"] - be_offs*point
                pos["sl"] = be_px
                pos["be_done"] = True

            # Trailing stop
            if gain_pts > ts_start:
                if pos["dir"]>0:
                    new_sl = px_close - (gain_pts - ts_step)*point
                    if new_sl > (pos["sl"] or -1e18): pos["sl"] = new_sl
                else:
                    new_sl = px_close + (gain_pts - ts_step)*point
                    if (pos["sl"] is None) or (new_sl < pos["sl"]): pos["sl"] = new_sl

            # تحقّق SL/TP على حدود الشمعة الحالية
            hit_tp = (px_high >= pos["tp"]) if pos["dir"]>0 else (px_low <= pos["tp"])
            hit_sl = (px_low  <= pos["sl"]) if pos["dir"]>0 else (px_high >= pos["sl"])

            exit_reason = None
            exit_px = None
            if hit_tp and hit_sl:
                # لو لمس الاثنان، نفترض SL/TP بنفس الشمعة: نغلق على الأسوأ (محافظ)
                exit_reason = "SL&TP"
                exit_px = pos["sl"] if pos["dir"]>0 else pos["sl"]
            elif hit_tp:
                exit_reason = "TP"
                exit_px = pos["tp"]
            elif hit_sl:
                exit_reason = "SL"
                exit_px = pos["sl"]

            if exit_reason:
                # حساب الربح بالنقود على أساس ticks
                ticks = ((exit_px - pos["open_px"])/tick_size) if pos["dir"]>0 else ((pos["open_px"] - exit_px)/tick_size)
                profit = ticks * tick_value
                trades.append({
                    "ts": df["Time"].iloc[i],
                    "type": ("BUY" if pos["dir"]>0 else "SELL"),
                    "price_open": pos["open_px"],
                    "price_close": exit_px,
                    "sl_pts": pos["sl_pts"],
                    "tp_pts": pos["tp_pts"],
                    "profit": profit,
                    "reason": exit_reason
                })
                pos = None

        # فتح مركز جديد
        if pos is None:
            dir_ = 0
            if buy_sig.iloc[i]:  dir_ = +1
            elif sell_sig.iloc[i]: dir_ = -1
            if dir_ != 0 and at>0:
                sl_pts = max(atr_mult*at, 10.0)         # نقاط
                tp_pts = max(sl_pts*rr, 10.0)           # نقاط
                sl = px_open - sl_pts*point if dir_>0 else px_open + sl_pts*point
                tp = px_open + tp_pts*point if dir_>0 else px_open - tp_pts*point
                pos = {
                    "dir": dir_,
                    "open_px": px_open,
                    "sl_pts": sl_pts,
                    "tp_pts": tp_pts,
                    "sl": sl,
                    "tp": tp,
                    "be_done": False
                }

    # إحصائيات
    df_tr = pd.DataFrame(trades)
    if df_tr.empty:
        return df_tr, {"trades":0,"winrate":0.0,"pf":0.0,"net":0.0,"maxdd":0.0}

    prof = df_tr["profit"].astype(float).fillna(0.0)
    wins = int((prof>0).sum()); losses = int((prof<0).sum()); trades_n = wins+losses
    winrate = round(100.0*wins/trades_n,2) if trades_n>0 else 0.0
    gw = float(prof[prof>0].sum()); gl = float(-prof[prof<0].sum())
    pf = (gw/gl) if gl>0 else float("inf")
    pf = 0.0 if pf==float("inf") else round(pf,2)

    eq = prof.cumsum().values.tolist()
    peak = -1e18; mdd = 0.0
    for v in eq:
        if v>peak: peak=v
        dd = peak - v
        if dd>mdd: mdd = dd

    return df_tr, {"trades":trades_n,"winrate":winrate,"pf":pf,"net":float(round(prof.sum(),2)),"maxdd":float(round(mdd,2))}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="CSV: Time,Open,High,Low,Close")
    ap.add_argument("--outdir", default="artifacts", help="مجلد المخرجات")
    # شبكة بسيطة
    ap.add_argument("--atr",  nargs="+", default=["1.8","2.0","2.2"])
    ap.add_argument("--rr",   nargs="+", default=["1.6","2.0","2.4"])
    ap.add_argument("--mc",   nargs="+", default=["20","30"]) # MAfast
    ap.add_argument("--ms",   nargs="+", default=["50","80"]) # MAslow
    args = ap.parse_args()

    df = pd.read_csv(args.data, parse_dates=["Time"])
    df = df.dropna().sort_values("Time").reset_index(drop=True)

    combos = []
    for a in args.atr:
        for r in args.rr:
            for mf in args.mc:
                for ms in args.ms:
                    combos.append((float(a), float(r), int(mf), int(ms)))

    rows = []
    best = None
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    for (a, r, mf, ms) in combos:
        params = dict(
            MAfast=mf, MAslow=ms, RSI_Period=14, RSI_BuyMax=60, RSI_SellMin=40,
            ATR_Period=14, ATR_SL_Mult=a, RR=r,
            BE_Trig=80, BE_Offs=15, TS_Start=250, TS_Step=80,
            Point=0.1, TickValue=1.0, TickSize=0.1
        )
        tr, stats = run_bt(df, params)
        rows.append({"ATR":a,"RR":r,"MAfast":mf,"MAslow":ms, **stats})
        if (best is None) or (stats["pf"]>best["pf"]) or (stats["pf"]==best["pf"] and stats["net"]>best["net"]):
            best = {**stats, **params}

    res = pd.DataFrame(rows)
    res_path = outdir/"bt_results.csv"
    res.to_csv(res_path, index=False, encoding="utf-8-sig")

    best_path = outdir/"best_result.json"
    with open(best_path, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2)

    print("✅ saved:", res_path)
    print("✅ saved:", best_path)
    print("⭐ BEST:", best)

if __name__ == "__main__":
    main()





    