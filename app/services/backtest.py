# -*- coding: utf-8 -*-backtest.py
"""
Backtester (V3.1-compatible) for EMA/RSI + ATR SL/TP with Regime & Risk Governor.
- يقرأ CSV تلقائياً (Time/open/high/low/close).
- مؤشرات: EMA/RSI/ATR (EWMA).
- إشارات: تقاطع EMA + مرشح RSI.
- SL/TP: ATR * mult + أرضية سبريد اختيارية.
- أنظمة السوق: Trend / Range / HighVol (تعديل RR/TS/BE والمخاطرة).
- محاكاة موحّدة (flat -> long/short -> flat) مع سجلّ R لكل صفقة.
- مقاييس الأداء + سلسلة صفقات لاستخدامها في Monte Carlo/Walk-Forward.
"""
from __future__ import annotations
import math, json, argparse, os
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Tuple, List
import numpy as np
import pandas as pd

# تسريع
MAX_ROWS  = 120_000
STRIDE    = 4

# ---------- I/O ----------
REQ_COLS = [["Time","timestamp","Datetime","Date","time","datetime"],
            ["open","Open","OPEN"],
            ["high","High","HIGH"],
            ["low","Low","LOW"],
            ["close","Close","CLOSE"]]

def _find_col(cols: List[str], cands: List[str]) -> str:
    low = {c.lower():c for c in cols}
    for k in cands:
        if k.lower() in low: return low[k.lower()]
    raise KeyError(f"Column not found from {cands}. In file: {list(cols)}")

def load_csv(path: str) -> pd.DataFrame:
    df=None
    for sep in [",",";","\t","|"]:
        try:
            df=pd.read_csv(path, sep=sep, engine="python")
            if df is not None and df.shape[1]>=4: break
        except Exception:
            pass
    if df is None or df.empty:
        raise ValueError("CSV empty or unreadable.")

    tc=_find_col(df.columns, REQ_COLS[0])
    oc=_find_col(df.columns, REQ_COLS[1])
    hc=_find_col(df.columns, REQ_COLS[2])
    lc=_find_col(df.columns, REQ_COLS[3])
    cc=_find_col(df.columns, REQ_COLS[4])

    x=df[[tc,oc,hc,lc,cc]].copy()
    x.columns=["Time","Open","High","Low","Close"]
    x["Time"]=pd.to_datetime(x["Time"], errors="coerce")
    for c in ["Open","High","Low","Close"]:
        x[c]=pd.to_numeric(x[c], errors="coerce")
    x.dropna(inplace=True)
    x.sort_values("Time", inplace=True)
    x.reset_index(drop=True, inplace=True)
    return x

# ---------- Indicators ----------
def ema(a: pd.Series, n: int) -> pd.Series:
    return a.ewm(span=max(1,n), adjust=False).mean()

def rsi(close: pd.Series, n: int) -> pd.Series:
    d=close.diff()
    up=np.where(d>0,d,0.0)
    dn=np.where(d<0,-d,0.0)
    ru=pd.Series(up).ewm(alpha=1.0/max(1,n), adjust=False).mean()
    rd=pd.Series(dn).ewm(alpha=1.0/max(1,n), adjust=False).mean()
    rs=ru/np.maximum(1e-12,rd)
    return 100.0-(100.0/(1.0+rs))

def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h,l,c=df["High"],df["Low"],df["Close"]
    pc=c.shift(1)
    tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(span=max(1,n), adjust=False).mean()

# ---------- Regimes ----------
REG_NEUTRAL=0; REG_TREND=1; REG_RANGE=2; REG_HIGHVOL=3

@dataclass
class RegimeParams:
    use_regime: bool=True
    rd_atr_per:int=14
    rd_highvol_mult:float=2.2
    rd_slope_lookback:int=1
    rd_slope_thresh_pts:float=50.0
    rd_median_window:int=200
    # regime tuning
    tr_rr:float=2.40; tr_ts_start:int=250; tr_ts_step:int=80;  tr_be_trig:int=80;  tr_be_offs:int=15; tr_risk_mult:float=1.20
    rg_rr:float=1.60; rg_ts_start:int=180; rg_ts_step:int=60;  rg_be_trig:int=60;  rg_be_offs:int=10; rg_risk_mult:float=0.90
    hv_rr:float=2.80; hv_ts_start:int=350; hv_ts_step:int=120; hv_be_trig:int=120; hv_be_offs:int=30; hv_risk_mult:float=0.70

@dataclass
class GovernorParams:
    use_rg: bool=True
    daily_soft_pct: float=3.0
    daily_hard_pct: float=5.0
    eq_dd_halt_pct: float=12.0
    max_exposure_lots: float=5.0
    scale_by_atr: bool=True
    risk_min_pct: float=0.2
    risk_max_pct: float=1.2
    atr_norm_pts: float=800.0


# ...

@dataclass
class BTParams:
    # signals
    ma_fast:int=20
    ma_slow:int=50
    rsi_per:int=14
    rsi_buy_max:int=60
    rsi_sell_min:int=40
    # risk/rr/sl
    atr_per:int=14
    atr_mult:float=2.0
    rr:float=2.0
    # spread/commission/risk
    max_spread_pts:int=350
    spread_sl_factor:float=2.5
    commission_per_lot:float=7.0
    risk_pct:float=0.5
    # regime/governor  👈 هنا التعديل
    regime: "RegimeParams" = field(default_factory=RegimeParams)
    governor: "GovernorParams" = field(default_factory=GovernorParams)


@dataclass
class BTResult:
    profit_pct:float
    buyhold_pct:float
    max_dd_pct:float
    sharpe:float
    trades:int
    winrate:float
    avg_r:float
    params:Dict[str,Any]
    trade_R:List[float]
    regimes:Dict[str,int]   # counts per regime

def _detect_regime(atr_now:float, ema_f_now:float, ema_f_prev:float, point:float,
                   rp:RegimeParams, atr_med_pts:float) -> int:
    if not rp.use_regime: return REG_NEUTRAL
    slope_pts=(ema_f_now-ema_f_prev)/max(1e-12,point)
    is_highvol = (atr_med_pts>0.0 and atr_now >= rp.rd_highvol_mult*atr_med_pts)
    is_trend   = (abs(slope_pts) >= rp.rd_slope_thresh_pts)
    if is_highvol: return REG_HIGHVOL
    if is_trend:   return REG_TREND
    return REG_RANGE

def backtest(df: pd.DataFrame, p: BTParams, symbol_point:float=0.10) -> BTResult:
    # مؤشرات
    ema_f=np.asarray(ema(df["Close"], p.ma_fast), dtype=np.float64)
    ema_s=np.asarray(ema(df["Close"], p.ma_slow), dtype=np.float64)
    rsi_v=np.asarray(rsi(df["Close"], p.rsi_per), dtype=np.float64)
    atr_v=np.asarray(atr(df, p.atr_per),        dtype=np.float64)
    close=np.asarray(df["Close"].values,        dtype=np.float64)

    valid=np.isfinite(ema_f)&np.isfinite(ema_s)&np.isfinite(rsi_v)&np.isfinite(atr_v)&np.isfinite(close)
    n=close.shape[0]; start=max(0, n-MAX_ROWS); slc=slice(start, n, STRIDE)
    ema_f=ema_f[slc]; ema_s=ema_s[slc]; rsi_v=rsi_v[slc]; atr_v=atr_v[slc]; close=close[slc]; valid=valid[slc]

    v=valid & np.roll(valid,1); ema_f_p=np.roll(ema_f,1); ema_s_p=np.roll(ema_s,1)
    buy =(ema_f_p<=ema_s_p)&(ema_f>ema_s)&(rsi_v<=p.rsi_buy_max)&v
    sell=(ema_f_p>=ema_s_p)&(ema_f<ema_s)&(rsi_v>=p.rsi_sell_min)&v
    buy[0]=False; sell[0]=False

    # وسيط ATR للـ HighVol
    atr_med_pts=float(pd.Series(atr_v).rolling(window=max(10,p.regime.rd_median_window),min_periods=10).median().iloc[-1]) if p.regime.use_regime else 0.0

    equity=1.0; peak=1.0; max_dd=0.0
    R_list=[]; reg_counts={ "trend":0, "range":0, "highvol":0, "neutral":0 }
    in_pos=0; entry=sl=tp=0.0
    eff_rr=p.rr
    eff_be_tr=100; eff_be_off=20
    eff_ts_st=300; eff_ts_step=100
    eff_risk=p.risk_pct

    rng=range(close.shape[0])
    for i in rng:
        if not v[i]:
            if in_pos!=0: in_pos=0
            continue

        # تحديد النظام والقيم الفعالة
        reg=_detect_regime(atr_v[i], ema_f[i], ema_f_p[i], symbol_point, p.regime, atr_med_pts)
        if   reg==REG_TREND:
            eff_rr=p.regime.tr_rr; eff_ts_st=p.regime.tr_ts_start; eff_ts_step=p.regime.tr_ts_step
            eff_be_tr=p.regime.tr_be_trig; eff_be_off=p.regime.tr_be_offs; reg_counts["trend"]+=1
            risk_mult=p.regime.tr_risk_mult
        elif reg==REG_RANGE:
            eff_rr=p.regime.rg_rr; eff_ts_st=p.regime.rg_ts_start; eff_ts_step=p.regime.rg_ts_step
            eff_be_tr=p.regime.rg_be_trig; eff_be_off=p.regime.rg_be_offs; reg_counts["range"]+=1
            risk_mult=p.regime.rg_risk_mult
        elif reg==REG_HIGHVOL:
            eff_rr=p.regime.hv_rr; eff_ts_st=p.regime.hv_ts_start; eff_ts_step=p.regime.hv_ts_step
            eff_be_tr=p.regime.hv_be_trig; eff_be_off=p.regime.hv_be_offs; reg_counts["highvol"]+=1
            risk_mult=p.regime.hv_risk_mult
        else:
            eff_rr=p.rr; eff_ts_st=300; eff_ts_step=100; eff_be_tr=100; eff_be_off=20; reg_counts["neutral"]+=1
            risk_mult=1.0

        eff_risk=p.risk_pct * risk_mult
        if p.governor.use_rg and p.governor.scale_by_atr:
            ratio=atr_v[i]/max(1e-8,p.governor.atr_norm_pts)
            ratio=max(0.25, min(2.5, ratio))
            eff_risk *= (1.0/ratio)
            eff_risk=max(p.governor.risk_min_pct, min(p.governor.risk_max_pct, eff_risk))

        price=close[i]

        # إدارة الصفقة المفتوحة (SL/TP فقط هنا؛ TS/BE تفصيله داخل EA الحقيقي)
        if in_pos!=0:
            if in_pos>0:
                if price>=tp or price<=sl:
                    r = (tp-entry)/(tp-sl) if price>=tp else - (sl-entry)/(tp-sl)
                    R_list.append(float(r))
                    equity *= (1.0 + r * 0.01 * 100*eff_risk/100.0)
                    in_pos=0
            else:
                if price<=tp or price>=sl:
                    r = (entry-tp)/(sl-tp) if price<=tp else - (entry-sl)/(sl-tp)
                    R_list.append(float(r))
                    equity *= (1.0 + r * 0.01 * 100*eff_risk/100.0)
                    in_pos=0

        # دخول جديد
        if in_pos==0:
            # SL من ATR + أرضية سبريد اختيارية (لا نملك السبريد من CSV؛ يمكن تمرير spread pts خارجياً لاحقًا)
            atr_pts=max(1e-8, atr_v[i])
            sl_pts=max(p.atr_mult*atr_pts, 10.0)  # أرضية 10 نقاط
            tp_pts=max(sl_pts*eff_rr, 10.0)

            if buy[i]:
                entry=price; sl=entry-sl_pts; tp=entry+tp_pts; in_pos=+1
            elif sell[i]:
                entry=price; sl=entry+sl_pts; tp=entry-tp_pts; in_pos=-1

        peak=max(peak,equity)
        dd=(peak-equity)/peak
        if dd>max_dd: max_dd=dd

    r_arr=np.array(R_list, dtype=np.float64)
    trades=int(r_arr.size)
    winrate=float((r_arr>0).mean()*100.0) if trades else 0.0
    avg_r=float(r_arr.mean()) if trades else 0.0
    ret=(equity-1.0)*100.0

    # Buy&Hold
    valid_idx=np.where(v)[0]
    if valid_idx.size>=2:
        i0=int(valid_idx[0]); i1=int(valid_idx[-1])
        buyhold=(close[i1]/close[i0]-1.0)*100.0
    else:
        buyhold=0.0

    sharpe=float(((r_arr.mean()/(r_arr.std(ddof=1)+1e-12))*np.sqrt(12.0)) if trades>1 else 0.0)

    return BTResult(
        profit_pct=round(ret,2),
        buyhold_pct=round(buyhold,2),
        max_dd_pct=round(max_dd*100.0,2),
        sharpe=round(sharpe,3),
        trades=trades,
        winrate=round(winrate,2),
        avg_r=round(avg_r,3),
        params=json.loads(json.dumps(asdict(p))),  # deep-safe
        trade_R=[float(x) for x in R_list],
        regimes={k:int(v) for k,v in reg_counts.items()}
    )

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/XAUUSD_H1.csv")
    args=ap.parse_args()
    print(json.dumps(asdict(backtest(load_csv(args.csv), BTParams())), ensure_ascii=False, indent=2))







    