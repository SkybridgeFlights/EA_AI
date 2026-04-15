# -*- coding: utf-8 -*-reporting.py
from __future__ import annotations
import os, json, datetime as dt
from typing import Dict, Any, Optional, List
from pathlib import Path

# ---- env defaults ----
_ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts")).resolve()
_ROTATE_MAX_MB = float(os.getenv("LOG_MAX_MB", "64"))  # 64MB افتراضيًا

def _utc_iso(ts: Optional[dt.datetime]=None) -> str:
    ts = ts or dt.datetime.utcnow()
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

def _today_key() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d")

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _size_mb(p: Path) -> float:
    try:
        return p.stat().st_size / (1024 * 1024)
    except Exception:
        return 0.0

def _append_jsonl_rotating(base_path: Path, obj: Dict[str, Any], max_mb: float) -> Path:
    """
    يضيف سطر JSONL مع تدوير تلقائي عند تجاوز الحجم المحدد.
    يستخدم base.jsonl ثم base.jsonl.1 و .2 ... إلخ.
    """
    _ensure_dir(base_path.parent)

    target = base_path
    if target.exists() and _size_mb(target) >= max_mb:
        i = 1
        while True:
            cand = base_path.with_suffix(base_path.suffix + f".{i}")
            if not cand.exists() or _size_mb(cand) < max_mb:
                target = cand
                break
            i += 1

    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
        f.write("\n")
    return target

# ---------------- public API ----------------

def append_policy_report(root_dir: Optional[str],
                         metrics: Dict[str, Any],
                         prev_params: Optional[Dict[str, Any]],
                         new_params: Dict[str, Any],
                         explain: Dict[str, Any]) -> str:
    """
    يكتب سطرًا في: {root}/policy_reports/policy_report_YYYYMMDD.jsonl
    مع تدوير تلقائي فوق LOG_MAX_MB.
    """
    root = Path(root_dir).resolve() if root_dir else _ARTIFACTS_DIR
    out_dir = root / "policy_reports"
    out_file = out_dir / f"policy_report_{_today_key()}.jsonl"

    rec = {
        "ts": _utc_iso(),
        "metrics": metrics,
        "prev": prev_params,
        "new": new_params,
        "explain": explain
    }
    written = _append_jsonl_rotating(out_file, rec, _ROTATE_MAX_MB)
    return str(written)

def append_audit_entry(root_dir: Optional[str],
                       policy: Dict[str, Any],
                       policy_hash: Optional[str]=None,
                       extra: Optional[Dict[str, Any]]=None) -> str:
    """
    يسجل قرارًا في: {root}/decisions/audit.jsonl
    """
    root = Path(root_dir).resolve() if root_dir else _ARTIFACTS_DIR
    out_dir = root / "decisions"
    out_file = out_dir / "audit.jsonl"

    rec = {
        "ts": _utc_iso(),
        "policy_hash": policy_hash,
        "policy": policy
    }
    if extra:
        rec["extra"] = extra

    written = _append_jsonl_rotating(out_file, rec, _ROTATE_MAX_MB)
    return str(written)

def read_last_lines(path: str, n: int=100) -> List[str]:
    """
    يقرأ آخر n أسطر من ملف JSONL دون تحميل الملف كاملًا.
    مفيد للوحة التحكم.
    """
    p = Path(path)
    if not p.exists():
        return []
    # قراءة فعّالة من النهاية
    out: List[str] = []
    with p.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block = 8192
        buf = b""
        pos = size
        while pos > 0 and len(out) < n:
            step = block if pos - block > 0 else pos
            pos -= step
            f.seek(pos, os.SEEK_SET)
            buf = f.read(step) + buf
            lines = buf.split(b"\n")
            # اترك أول عنصر لأنّه قد يكون مكسورًا وسيُستكمل في الدورة التالية
            buf = lines[0]
            for line in lines[-1:0:-1]:
                if line.strip():
                    out.append(line.decode("utf-8", errors="ignore"))
                if len(out) >= n:
                    break
    out.reverse()
    return out








