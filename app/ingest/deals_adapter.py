# app/ingest/deals_adapter.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, os, json, datetime as dt
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.config import settings

def _to_float(x, d=None):
    if x is None: return d
    if isinstance(x,(int,float)): return float(x)
    s=str(x).strip().strip('"').strip("'")
    try: return float(s)
    except: return d

def _to_int(x, d=None):
    if x is None: return d
    if isinstance(x,(int,float)): return int(float(x))
    s=str(x).strip().strip('"').strip("'")
    try: return int(float(s))
    except: return d

def _parse_time(x) -> Optional[str]:
    if not x: return None
    s=str(x).strip()
    # try epoch
    if s.isdigit():
        try:
            t=dt.datetime.utcfromtimestamp(int(s))
            return t.strftime("%Y-%m-%dT%H:%M:%SZ")
        except: pass
    # try MT5 "YYYY.MM.DD HH:MM:SS"
    try:
        t=dt.datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
        return (t.replace(tzinfo=None)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except: pass
    # try ISO-ish
    try:
        s2=s.replace("Z","").replace("T"," ")
        t=dt.datetime.fromisoformat(s2)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    except: pass
    return None

def _month_key(ts_iso: str) -> str:
    # ts_iso = "YYYY-MM-DDTHH:MM:SSZ"
    y=int(ts_iso[0:4]); m=int(ts_iso[5:7])
    return f"{y:04d}{m:02d}"

def _read_deals(path: str) -> List[Dict[str,Any]]:
    out: List[Dict[str,Any]]=[]
    p=Path(path)
    if not p.exists(): return out
    with p.open("r", encoding="utf-8", newline="") as f:
        sniffer_sample=f.read(2048)
        f.seek(0)
        try:
            dialect=csv.Sniffer().sniff(sniffer_sample) if sniffer_sample else csv.excel
        except:
            dialect=csv.excel
        r=csv.DictReader(f, dialect=dialect)
        for row in r:
            out.append(row)
    return out

def _row_to_jsonl(rec: Dict[str,Any]) -> Optional[Dict[str,Any]]:
    typ=(rec.get("type") or rec.get("Type") or "").strip().upper()
    if typ not in ("BUY","SELL"): return None

    profit=_to_float(rec.get("profit") or rec.get("Profit"), None)
    # نحتفظ بكل الصفقات المغلقة حتى لو الربح 0 (للسجل)، لكن نتأكد من وجود وقت
    ts=_parse_time(rec.get("ts") or rec.get("time") or rec.get("Time"))
    if ts is None: return None

    # حقول اختيارية من EA JSONL إن وُجدت في CSV
    mfe=_to_float(rec.get("mfe_pts"), None)
    mae=_to_float(rec.get("mae_pts"), None)
    slip=_to_float(rec.get("slippage_pts"), None)
    spop=_to_float(rec.get("spread_open"), None)
    R=_to_float(rec.get("R"), None)

    return {
        "time": ts,
        "symbol": (rec.get("symbol") or rec.get("Symbol") or settings.SYMBOL).upper(),
        "type": typ,
        "lots": _to_float(rec.get("lots") or rec.get("Lots"), None),
        "price_open": _to_float(rec.get("price_open") or rec.get("PriceOpen") or rec.get("price") or rec.get("price_open"), None),
        "price_close": _to_float(rec.get("price_close") or rec.get("PriceClose"), None),
        "profit": profit,
        "slippage_pts": slip,
        "spread_open_pts": spop,
        "R": R,
        "mfe_pts": mfe,
        "mae_pts": mae,
        "account_ccy": (rec.get("account_ccy") or "USD").upper()
    }

def run(deals_csv: str=None, out_dir: str=None, prefix: str=None) -> Dict[str,Any]:
    deals_csv = deals_csv or settings.DEALS_CSV_PATH
    out_dir   = out_dir or settings.JSONL_DIR
    prefix    = prefix or settings.JSONL_FILE_PREFIX

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    rows=_read_deals(deals_csv)
    total_in=len(rows)
    written=0
    by_month: Dict[str, List[Dict[str,Any]]] = {}

    for r in rows:
        j=_row_to_jsonl(r)
        if not j: continue
        mk=_month_key(j["time"])
        by_month.setdefault(mk, []).append(j)

    # إلحاق ذرّي لكل شهر
    for mk, items in by_month.items():
        dest=Path(out_dir) / f"{prefix}{mk}.jsonl"
        with dest.open("a", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False))
                f.write("\n")
                written+=1

    report={
        "ok": True,
        "source": deals_csv,
        "out_dir": out_dir,
        "prefix": prefix,
        "input_rows": total_in,
        "written": written,
        "months": sorted(by_month.keys())
    }
    # تقرير بسيط
    artifacts_dir=Path(settings.ARTIFACTS_DIR)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    rpt_path=artifacts_dir / f"ingest_report_{dt.datetime.utcnow().strftime('%Y%m%d')}.txt"
    rpt_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

if __name__=="__main__":
    out=run()
    print(json.dumps(out, ensure_ascii=False, indent=2))




