# app/ingest/quality_check.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, datetime as dt
from pathlib import Path
from typing import Dict, Any, List
from app.config import settings

def scan_jsonl_month(month_key: str) -> Dict[str,Any]:
    dest=Path(settings.JSONL_DIR) / f"{settings.JSONL_FILE_PREFIX}{month_key}.jsonl"
    res={"file": str(dest), "exists": dest.exists(), "rows": 0, "min_ts": None, "max_ts": None}
    if not dest.exists(): return res
    seen=set()
    min_ts=None; max_ts=None; rows=0
    with dest.open("r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            rows+=1
            if line in seen: continue
            seen.add(line)
            # لا نحمّل json كاملًا هنا، نقرأ الطابع الزمني بحذر
            try:
                ts=line.split('"time":"')[1].split('"',1)[0]
                if min_ts is None or ts<min_ts: min_ts=ts
                if max_ts is None or ts>max_ts: max_ts=ts
            except:
                pass
    res["rows"]=rows; res["min_ts"]=min_ts; res["max_ts"]=max_ts
    return res

if __name__=="__main__":
    # اليوم الحالي
    mk=dt.datetime.utcnow().strftime("%Y%m")
    out=scan_jsonl_month(mk)
    print(json.dumps(out, ensure_ascii=False, indent=2))







