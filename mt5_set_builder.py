import os, glob, json, math, statistics as stats
from pathlib import Path
from datetime import datetime

# -------------------- Config --------------------
# مفاتيح سندرسها ونبني لها نطاقات
FLOAT_KEYS = {
    "rr":              {"name":"InpRR",            "round":0.1, "min":0.6,  "max":4.0},
    "risk_pct":        {"name":"InpRiskPct",       "round":0.1, "min":0.1,  "max":3.0},
}
INT_KEYS = {
    "ts_start":        {"name":"TS_StartPts",      "round":5,   "min":30,    "max":2000},
    "ts_step":         {"name":"TS_StepPts",       "round":5,   "min":5,     "max":1500},
    "be_trig":         {"name":"BE_TriggerPts",    "round":5,   "min":10,    "max":1500},
    "be_offs":         {"name":"BE_OffsetPts",     "round":5,   "min":5,     "max":600},
    "max_spread_pts":  {"name":"InpMaxSpreadPts",  "round":5,   "min":50,    "max":5000},
}
# مفاتيح إضافية نضعها بقيم ثابتة مع تفعيل/تعطيل المعايرة
FIXED_BOOL = {
    "UseTrailingStop": True,
    "UseBreakEven": True,
    "TradeOnClosedBar": True,
}
FIXED_INT = {
    "MaxOpenPerSymbol": 1,
    "MaxTradesPerDay":  50,
    "InpATR_Period":    14,
    "InpMAfast":        20,
    "InpMAslow":        50,
    "InpRSI_Period":    14,
}
FIXED_FLOAT = {
    "InpATR_SL_Mult":  2.0,
    "InpCommissionPerLot": 7.0,
    "InpSpreadSLFactor": 2.5,
    "AI_MinConfidence": 0.6,  # لو احتجته لاحقًا في EA الكامل
}

# -------------------- Helpers --------------------
def env_common_files():
    for k in ["MT5_COMMON_FILES","COMMON_FILES_DIR"]:
        p = os.getenv(k)
        if p and Path(p).exists():
            return Path(p)
    # default Windows path
    default = Path(r"C:\Users\Wajd Shaaban\AppData\Roaming\MetaQuotes\Terminal\Common\Files")
    return default if default.exists() else None

def read_jsonl_values(common_dir):
    vals = []
    for fp in glob.glob(str(common_dir / "trades_*.jsonl")):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line=line.strip()
                    if not line: continue
                    try:
                        o=json.loads(line)
                    except Exception:
                        continue
                    # نحاول قراءة المفاتيح إن وجدت
                    item = {}
                    for k in FLOAT_KEYS.keys():
                        if k in o and isinstance(o[k], (int,float)):
                            item[k] = float(o[k])
                    for k in INT_KEYS.keys():
                        if k in o and isinstance(o[k], (int,float)):
                            item[k] = int(round(float(o[k])))
                    # بدائل أسماء الحقول إذا كانت بالحروف المختلفة
                    # من سجلات الفتح في EA لديك: rr, risk_pct, ts_start, ts_step, be_trig, be_offs, max_spread_pts
                    # أحيانًا تأتي بصيغة أخرى، نضيف fallback من أسماء معروفة:
                    alt_map = {
                        "rr": ["RR","E_RR","rr_eff"],
                        "risk_pct": ["risk","risk_used","E_RiskPct"],
                        "ts_start": ["TS_StartPts","ts_start_points"],
                        "ts_step":  ["TS_StepPts","ts_step_points"],
                        "be_trig":  ["BE_TriggerPts","be_trigger_points"],
                        "be_offs":  ["BE_OffsetPts","be_offset_points"],
                        "max_spread_pts": ["max_spread","max_spread_points","G_MaxSpreadPts"]
                    }
                    for std,alts in alt_map.items():
                        if std not in item:
                            for alt in alts:
                                if alt in o and isinstance(o[alt], (int,float)):
                                    v = float(o[alt])
                                    item[std] = int(round(v)) if std in INT_KEYS else float(v)
                                    break
                    if item:
                        vals.append(item)
        except Exception:
            continue
    return vals

def q20_q80_pad(arr, pad=0.10):
    if len(arr)==0:
        return None
    arr_sorted = sorted(arr)
    def q(p):
        idx = (len(arr_sorted)-1)*p
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo==hi: return arr_sorted[lo]
        return arr_sorted[lo] + (arr_sorted[hi]-arr_sorted[lo])*(idx-lo)
    q20 = q(0.20)
    q80 = q(0.80)
    span = q80 - q20
    if span <= 0:
        span = abs(q80) * 0.2 if q80!=0 else 1.0
    start = q20 - pad*span
    stop  = q80 + pad*span
    return start, stop

def round_to(v, step):
    if step<=0: return v
    return round(v/step)*step

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def mk_range(values, meta):
    # values: قائمة أرقام
    # meta: {name, round, min, max}
    r = q20_q80_pad(values)
    if not r:
        # fallback افتراضي مع نطاق معقول
        start, stop = meta["min"], meta["max"]
    else:
        start, stop = r
        start = clamp(start, meta["min"], meta["max"])
        stop  = clamp(stop,  meta["min"], meta["max"])
        if stop <= start:
            stop = clamp(start + (meta["max"]-meta["min"])*0.2, meta["min"], meta["max"])
    # خطوة تقريبية 15 قسمة
    step = (stop - start)/15.0
    step = max(step, meta["round"])
    # تقريب وفق دقة الحقل
    start = round_to(start, meta["round"])
    stop  = round_to(stop,  meta["round"])
    step  = round_to(step,  meta["round"])
    # تأكد من اتساع النطاق
    if stop <= start:
        stop = start + meta["round"]*5
    return start, step, stop

def build_set_lines(suggest):
    """
    يبني سطور .set وفق تنسيق MT5:
    Param=Value
    Param,F=1      --> تفعيل Optimization
    Param,0=Start
    Param,1=Step
    Param,2=Stop
    """
    lines = []
    # ثوابت منطقية
    for k,v in FIXED_BOOL.items():
        lines.append(f"{k}={'1' if v else '0'}")
    # ثوابت أعداد صحيحة
    for k,v in FIXED_INT.items():
        lines.append(f"{k}={v}")
    # ثوابت كسرية
    for k,v in FIXED_FLOAT.items():
        lines.append(f"{k}={v}")

    # القيم المقترحة من البيانات
    for std_key, meta in FLOAT_KEYS.items():
        par = meta["name"]
        start, step, stop = suggest.get(std_key, (None,None,None))
        # قيمة افتراضية وسط النطاق
        default = round_to((start+stop)/2.0, meta["round"]) if start is not None else meta["min"]
        lines.append(f"{par}={default}")
        if start is not None:
            lines.append(f"{par},F=1")
            lines.append(f"{par},0={start}")
            lines.append(f"{par},1={step}")
            lines.append(f"{par},2={stop}")
        else:
            lines.append(f"{par},F=0")

    for std_key, meta in INT_KEYS.items():
        par = meta["name"]
        start, step, stop = suggest.get(std_key, (None,None,None))
        if start is not None:
            default = int(round((start+stop)/2.0))
            lines.append(f"{par}={default}")
            lines.append(f"{par},F=1")
            lines.append(f"{par},0={int(round(start))}")
            lines.append(f"{par},1={max(1, int(round(step)))}")
            lines.append(f"{par},2={int(round(stop))}")
        else:
            # قيمة افتراضية وسط المجال
            default = int(round((meta["min"]+meta["max"])/2.0))
            lines.append(f"{par}={default}")
            lines.append(f"{par},F=0")

    return lines

def main():
    common_dir = env_common_files()
    if not common_dir:
        print("ERROR: لم يتم العثور على Common\\Files. عرّف MT5_COMMON_FILES أو COMMON_FILES_DIR.")
        return

    rows = read_jsonl_values(common_dir)
    if not rows:
        print("تحذير: لم أعثر على trades_*.jsonl. سأبني نطاقات افتراضية.")
    # تجميع أعمدة
    col = {k:[] for k in list(FLOAT_KEYS.keys())+list(INT_KEYS.keys())}
    for r in rows:
        for k in col.keys():
            if k in r and r[k] is not None:
                col[k].append(r[k])

    suggest = {}
    for k,meta in FLOAT_KEYS.items():
        start, step, stop = mk_range(col[k], meta) if col[k] else (None,None,None)
        suggest[k] = (start, step, stop)
    for k,meta in INT_KEYS.items():
        start, step, stop = mk_range(col[k], meta) if col[k] else (None,None,None)
        if start is not None:
            start, step, stop = int(round(start)), max(1,int(round(step))), int(round(stop))
        suggest[k] = (start, step, stop)

    set_lines = build_set_lines(suggest)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_set = Path(f"suggested_ranges_{stamp}.set")
    with open(out_set, "w", encoding="utf-16le", newline="") as f:
        # MT5 يفضّل UTF-16LE غالبًا لملفات .set
        f.write("\r\n".join(set_lines))
    # CSV ملخص
    out_csv = Path(f"suggested_ranges_{stamp}.csv")
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        f.write("param,start,step,stop\n")
        for k,meta in FLOAT_KEYS.items():
            s = suggest[k]
            f.write(f"{meta['name']},{s[0]},{s[1]},{s[2]}\n")
        for k,meta in INT_KEYS.items():
            s = suggest[k]
            f.write(f"{meta['name']},{s[0]},{s[1]},{s[2]}\n")

    print(f"تم إنشاء: {out_set.name} و {out_csv.name}")
    print("استورد .set داخل MT5: Strategy Tester > Inputs > Load.")

if __name__ == "__main__":
    main()
