import argparse, subprocess, sys, shutil, os
from datetime import datetime
import pandas as pd

def find_dukascopy_cmd():
    # نستخدم npx أو الملف التنفيذي مباشرة إن وُجد
    if shutil.which("npx"):
        return ["npx", "dukascopy-node"]
    # محاولات مواقع شائعة على ويندوز
    candidates = [
        os.path.expandvars(r"%APPDATA%\npm\dukascopy-node.cmd"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\npm\dukascopy-node.cmd"),
        os.path.expandvars(r"%NVM_HOME%\nodejs\dukascopy-node.cmd"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return [c]
    raise FileNotFoundError("لم أجد npx أو dukascopy-node في PATH. ثبّت الحزمة: npm i -g dukascopy-node")

def fetch(symbol, tf, start, end, price, out_path):
    cmd = find_dukascopy_cmd() + [
        "--instrument", symbol.upper(),
        "--date-from", start.strftime("%Y-%m-%d"),
        "--date-to",   end.strftime("%Y-%m-%d"),
        "--timeframe", tf.lower(),          # مثل h1 أو m1
        "--price-type", price.lower(),      # bid أو ask
        "--format", "csv",
        "--out", out_path
    ]
    
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    print("Running:", " ".join(cmd))
    with open(out_path, "wb") as f:
        # نكتب المخرجات مباشرة إلى الملف
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        # أعرض الخطأ النصي بوضوح
        sys.stderr.write(proc.stderr.decode("utf-8", errors="ignore"))
        raise RuntimeError("فشل تنزيل البيانات من dukascopy-node")

    # تنظيف/تأكد من الأعمدة & الفرز
    try:
        df = pd.read_csv(out_path)
        # توحيد اسم التوقيت إن كان مختلفاً
        time_col = next((c for c in df.columns if c.lower() in ("time","timestamp","date")), None)
        if time_col:
            df.rename(columns={time_col: "time"}, inplace=True)
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
            df = df.dropna(subset=["time"]).sort_values("time").drop_duplicates("time")
            df.to_csv(out_path, index=False)
        print(f"Saved: {out_path}  rows={len(df)}")
    except Exception as e:
        print(f"Saved raw CSV (لم أقم بتنظيف الأعمدة): {out_path}  ({e})")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, help="مثال: XAUUSD")
    p.add_argument("--tf", required=True, help="الإطار الزمني: m1, m5, m15, m30, h1, d1...")
    p.add_argument("--start", required=True, help="مثال: 2023-01-01")
    p.add_argument("--end",   required=True, help="مثال: 2025-09-21")
    p.add_argument("--price", default="bid", help="bid أو ask")
    p.add_argument("--out",   required=True, help=r"مسار ملف CSV للخروج (مثل data\XAUUSD_H1.csv)")
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end   = datetime.strptime(args.end,   "%Y-%m-%d")
    fetch(args.symbol, args.tf, start, end, args.price, args.out)