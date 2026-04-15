# -*- coding: utf-8 -*-convert_mt5_csv.py
# محوّل CSV من MT5 إلى تنسيق موحّد: Time,Open,High,Low,Close
# يتحمّل تنويعات: فاصل (tab/;/,) – أعمدة بأقواس أو بدون – وقت في عمود واحد أو عمودين

import sys
import pandas as pd
from pathlib import Path

def _auto_read_csv(path: str) -> pd.DataFrame:
    """يحاول يكتشف الفاصل تلقائياً ويقرأ الملف بأفضل طريقة."""
    seps = ["\t", ";", ","]
    last_err = None
    for sep in seps:
        try:
            return pd.read_csv(path, sep=sep, engine="python")
        except Exception as e:
            last_err = e
            continue
    raise SystemExit(f"تعذّر قراءة الملف '{path}': {last_err}")

def _normalize_columns(cols):
    """
    يعيد قاموس {اسم_موحّد: اسم_حقيقي}
    أمثلة: '<OPEN>' أو 'OPEN' أو 'Open' كلها تُطابق 'OPEN'
    """
    norm = {}
    raw = list(cols)
    u = {c.replace("<","").replace(">","").strip().upper(): c for c in raw}
    for want in ["DATE","TIME","OPEN","HIGH","LOW","CLOSE","DATETIME"]:
        if want in u:
            norm[want] = u[want]
    return norm

def convert(in_path: str, out_path: str):
    df = _auto_read_csv(in_path)
    if df.empty:
        raise SystemExit("الملف فارغ.")

    colmap = _normalize_columns(df.columns)

    # 1) تكوين عمود Time
    if "DATETIME" in colmap:
        # بعض الإصدارات تصدّر عمود واحد يجمع التاريخ+الوقت
        time = pd.to_datetime(df[colmap["DATETIME"]], errors="coerce")
    else:
        if "DATE" not in colmap or "TIME" not in colmap:
            raise SystemExit(f"ملف غير متوقّع. الأعمدة المتاحة: {list(df.columns)}")
        time = pd.to_datetime(
            df[colmap["DATE"]].astype(str).str.strip() + " " +
            df[colmap["TIME"]].astype(str).str.strip(),
            errors="coerce"
        )

    # 2) أعمدة الأسعار
    need_price = ["OPEN","HIGH","LOW","CLOSE"]
    for k in need_price:
        if k not in colmap:
            raise SystemExit(f"عمود مفقود: {k}  | الأعمدة: {list(df.columns)}")

    out = pd.DataFrame({
        "Time":  time,
        "Open":  pd.to_numeric(df[colmap["OPEN"]], errors="coerce"),
        "High":  pd.to_numeric(df[colmap["HIGH"]], errors="coerce"),
        "Low":   pd.to_numeric(df[colmap["LOW"]], errors="coerce"),
        "Close": pd.to_numeric(df[colmap["CLOSE"]], errors="coerce"),
    })

    # 3) تنظيف وترتيب
    out = out.dropna(subset=["Time","Open","High","Low","Close"])
    out = out.sort_values("Time")
    out = out[~out["Time"].duplicated(keep="last")].reset_index(drop=True)

    # 4) تأكد من عدم وجود قيم غير رقمية/لانهاية
    out = out.replace([float("inf"), float("-inf")], pd.NA).dropna()

    # 5) حفظ
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"✅ تم الحفظ: {out_path}  | الصفوف: {len(out)} | من: {in_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("طريقة الاستخدام:\n  python -m tools.convert_mt5_csv <ملف_MT5.csv> data\\XAUUSD_H1.csv")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])







    