#build_signal_csv.py
import argparse, pandas as pd, numpy as np, datetime as dt
from pathlib import Path

# مثال بسيط (EMA/RSI) فقط لشرح البنية. بدّلها بمخرجات نموذجك ML إن أردت.
def make_signals(df):
    df["ema20"]=df["Close"].ewm(span=20).mean()
    df["ema50"]=df["Close"].ewm(span=50).mean()
    rsi_len=14
    delta=df["Close"].diff()
    up=delta.clip(lower=0).ewm(alpha=1/rsi_len, adjust=False).mean()
    dn=-delta.clip(upper=0).ewm(alpha=1/rsi_len, adjust=False).mean()
    rs = up/(dn.replace(0,np.nan))
    df["rsi"]=100-100/(1+rs)
    buy  =(df["ema20"].shift(1)<=df["ema50"].shift(1)) & (df["ema20"]>df["ema50"]) & (df["rsi"]<=60)
    sell =(df["ema20"].shift(1)>=df["ema50"].shift(1)) & (df["ema20"]<df["ema50"]) & (df["rsi"]>=40)
    sig=np.where(buy,1,np.where(sell,-1,0))
    conf=np.where(sig!=0,0.78,0.0)   # ضع ثقة نموذجك الحقيقي هنا
    hold=np.where(sig!=0,60,0)       # بالدقائق
    out=df.loc[sig!=0,["Time"]].copy()
    out["direction"]=np.where(sig[sig!=0]>0,"BUY","SELL")
    out["confidence"]=conf[sig!=0]
    out["hold_minutes"]=hold[sig!=0]
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--csv-prices",required=True,help="OHLCV H1: Time,Open,High,Low,Close,Volume")
    ap.add_argument("--symbol",default="XAUUSD")
    ap.add_argument("--out",default="ai_signals/xauusd_signals.csv")
    args=ap.parse_args()

    df=pd.read_csv(args.csv_prices,parse_dates=["Time"])
    sig=make_signals(df)
    sig["ts"]=sig["Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    sig["symbol"]=args.symbol
    sig[["ts","symbol","direction","confidence","hold_minutes"]].to_csv(args.out,index=False)
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    print(f"[ok] wrote {args.out}, rows={len(sig)}")

if __name__=="__main__":
    main()






