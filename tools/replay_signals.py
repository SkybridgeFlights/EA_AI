#reply_signals.py
import argparse, pandas as pd, time, os
from datetime import datetime

ap=argparse.ArgumentParser()
ap.add_argument("--csv",required=True)
ap.add_argument("--dst",required=True)  # Common\Files\ai_signals\xauusd_signal.ini
ap.add_argument("--speed",type=float,default=30.0) # كم ثانية حقيقية لكل ساعة تاريخية
args=ap.parse_args()

df=pd.read_csv(args.csv,parse_dates=["ts"])
i=0
os.makedirs(os.path.dirname(args.dst),exist_ok=True)
while i<len(df):
    row=df.iloc[i]
    # اكتب ini “كما لو أنه الآن”
    with open(args.dst,"w",encoding="utf-16-le") as f:
        f.write(
            f"ts={row['ts'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"symbol={row['symbol']}\n"
            f"direction={row['direction']}\n"
            f"confidence={row['confidence']:.2f}\n"
            f"hold_minutes={int(row['hold_minutes'])}\n"
        )
    print("[push]",row['ts'],row['direction'],row['confidence'])
    # نمذجة الزمن: انتقل للصف التالي بعد مهلة تتناسب مع الفارق الزمني التاريخي
    if i+1<len(df):
        dt_min=(df.iloc[i+1]["ts"]-row["ts"]).total_seconds()/3600.0
        time.sleep(max(0.1, dt_min/args.speed))
    i+=1






