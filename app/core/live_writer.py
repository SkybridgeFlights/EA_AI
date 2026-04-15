# app/core/live_writer.py

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_path(val: Union[str, Path]) -> Path:
    if isinstance(val, Path):
        return val
    return Path(str(val))


def _ensure_payload_from_obj(
    obj: Any,
    metrics_core: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    يقبل:
      - dict جاهز (الحالة الجديدة من selfcal_once)
      - أو model قديم فيه model_dump()/dict()
    ويعيد payload جاهز للكتابة.
    """
    # الحالة الحديثة: dict جاهز (كما نفعل الآن من policy_engine.selfcal_once)
    if isinstance(obj, dict):
        payload = dict(obj)  # shallow copy
    else:
        # دعم خلفي (backward compat) لو في المستقبل تم تمرير model
        # نحاول model_dump() أو dict()
        if hasattr(obj, "model_dump"):
            policy_dict = obj.model_dump()
        elif hasattr(obj, "dict"):
            policy_dict = obj.dict()
        else:
            raise TypeError(
                "Unsupported PolicyBody type; missing model_dump()/dict(), "
                "and not a dict instance."
            )

        updated_at = _now_utc_iso()
        policy_version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

        payload = {
            "schema_version": "1.0",
            "updated_at": updated_at,
            "policy_version": policy_version,
            "shadow": False,
            "policy": policy_dict,
            "core_metrics": metrics_core or {
                "pf": 0.0,
                "wr": 0.0,
                "maxdd": 0.0,
                "trades": 0,
                "pnl_today": 0.0,
            },
        }

    # تأكد أن الهيكل الأساسي موجود حتى لو جاء dict ناقص
    payload.setdefault("schema_version", "1.0")
    if "updated_at" not in payload:
        payload["updated_at"] = _now_utc_iso()
    if "policy_version" not in payload:
        payload["policy_version"] = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    payload.setdefault("shadow", False)

    # تأكد من وجود core_metrics حتى لو لم تُمرَّر
    core = dict(payload.get("core_metrics") or {})
    core.setdefault("pf", 0.0)
    core.setdefault("wr", 0.0)
    core.setdefault("maxdd", 0.0)
    core.setdefault("trades", 0)
    core.setdefault("pnl_today", 0.0)
    payload["core_metrics"] = core

    return payload


def _compute_checksum(payload: Dict[str, Any]) -> str:
    """
    نحسب checksum على الـ payload بدون حقل _write_meta
    لضمان أن الـ checksum يمثل محتوى الـ policy نفسه.
    """
    tmp = dict(payload)
    tmp.pop("_write_meta", None)
    text = json.dumps(tmp, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def write_policy(
    policy_or_payload: Any,
    out_path: Union[str, Path],
    mirror_path: Optional[Union[str, Path]] = None,
    metrics_core: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Dict[str, Any]:
    """
    الدالة الوحيدة المستخدمة من بقية المشروع.

    الآن تدعم:
      - dict مكتمل (الحالة الحالية من selfcal_once → normalized)
      - أو model قديم (Pydantic) عبر model_dump()/dict()

    وترجع الـ payload النهائي بعد إضافة:
      - _write_meta
      - checksum
    وتكتب نفس الـ JSON في:
      - out_path
      - mirror_path (لو ليس None)
    """
    out_p = _to_path(out_path)
    mirror_p = _to_path(mirror_path) if mirror_path else None

    # 1) تحويل الـ object إلى payload جاهز
    payload = _ensure_payload_from_obj(policy_or_payload, metrics_core=metrics_core)

    # 2) حساب checksum وإضافة _write_meta
    checksum = _compute_checksum(payload)

    write_meta = {
        "ok": True,
        "live_path": str(out_p),
        "mirror_path": str(mirror_p) if mirror_p is not None else None,
        "checksum": checksum,
        "policy_version": payload.get("policy_version"),
        "shadow": bool(payload.get("shadow", False)),
        # params الأساسية لتسهيل القراءة في الـ EA/logs
        "params": {},
    }

    # استخراج subset من important params لو موجودة
    try:
        params = payload.get("policy", {}).get("params", {})
        write_meta["params"] = {
            "rr": params.get("rr"),
            "risk_pct": params.get("risk_pct"),
            "ai_min_confidence": params.get("ai_min_confidence"),
        }
    except Exception:
        # لو الهيكل مختلف لا مشكلة، نترك params فارغة
        pass

    payload["_write_meta"] = write_meta

    # 3) كتابة الملف الرئيسي
    out_p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out_p.write_text(text, encoding="utf-8")

    # 4) كتابة mirror لو موجود
    if mirror_p is not None:
        mirror_p.parent.mkdir(parents=True, exist_ok=True)
        mirror_p.write_text(text, encoding="utf-8")

    return payload




