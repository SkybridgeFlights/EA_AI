# -*- coding: utf-8 -*-
"""
tools/sync_deals_to_jsonl.py

مهمة السكربت:
  - قراءة deals.csv (من settings.DEALS_CSV_PATH) بالشكل التالي:

    ts,symbol,type,lots,entry_price,exit_price,sl_pts,tp_pts,
    rr_eff,risk_pct,pnl_usd,R_eff,mfe_pts,mae_pts,slippage_pts,spread_pts,reason

  - تحويل الصفقات الجديدة فقط إلى JSONL منظم.
  - الكتابة إلى runtime/logs/trades_YYYYMM.jsonl (واحد لكل شهر).
  - تخزين حالة آخر صفّ تمت معالجته في runtime/logs/sync_state.json
    بحيث لا نعيد معالجة نفس الصفقة مرة أخرى.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.config import settings


# =========================
# إعدادات المسارات
# =========================

DEALS_CSV_PATH = Path(settings.DEALS_CSV_PATH)
LOGS_DIR = Path(settings.JSONL_DIR)
STATE_FILE = LOGS_DIR / "sync_state.json"


# =========================
# Helpers
# =========================

def _ensure_logs_dir() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_ts(s: str) -> Optional[datetime]:
    """
    يحاول قراءة حقل ts من deals.csv.
    يدعم:
      - رقم (ثواني منذ epoch).
      - أو نص ISO مثل 2025-11-28T10:35:00Z.
    """
    s = (s or "").strip()
    if not s:
        return None

    # أولاً: جرّب كـ epoch (int أو float)
    try:
        v = float(s)
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except Exception:
        pass

    # ثانيًا: ISO بسيط
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return float(s.replace(",", "."))
    except Exception:
        return None


def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _direction_from_type(t: str) -> str:
    s = (t or "").strip().lower()
    if "buy" in s:
        return "BUY"
    if "sell" in s:
        return "SELL"
    return "UNKNOWN"


def _month_key(dt: Optional[datetime]) -> str:
    if dt is None:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y%m")
    return dt.strftime("%Y%m")


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"last_row_index": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last_row_index": 0}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# تحميل live_config للحصول على policy meta
# =========================

def _load_live_policy_meta() -> Dict[str, Any]:
    """
    نقرأ live_config.json الحالي (إن وجد) لاستخراج:
      - policy_version
      - stability_mode
      - rr, risk_pct, ai_min_confidence
    """
    live_path = Path(getattr(settings, "LIVE_CONFIG_PATH", "runtime/live_config.json"))
    meta: Dict[str, Any] = {
        "policy_version": None,
        "stability_mode": None,
        "rr": None,
        "risk_pct": None,
        "ai_min_confidence": None,
    }
    if not live_path.exists():
        return meta

    try:
        data = json.loads(live_path.read_text(encoding="utf-8"))
    except Exception:
        return meta

    try:
        meta["policy_version"] = data.get("policy_version") or data.get("policy", {}).get("version")
    except Exception:
        pass

    try:
        params = data.get("policy", {}).get("params", {})
        meta["stability_mode"] = params.get("stability_mode")
        meta["rr"] = params.get("rr")
        meta["risk_pct"] = params.get("risk_pct")
        meta["ai_min_confidence"] = params.get("ai_min_confidence")
    except Exception:
        pass

    return meta


# =========================
# نواة التحميل من deals.csv
# =========================

@dataclass
class DealRow:
    row_index: int
    raw: Dict[str, Any]


 


# =========================
# تحويل صف CSV → JSONL dict
# =========================

def _deal_to_jsonl_obj(dr: DealRow, policy_meta: Dict[str, Any]) -> Dict[str, Any]:
    row = dr.raw

    ts_raw = row.get("ts") or row.get("TS") or ""
    ts_dt = _parse_ts(ts_raw)

    symbol = (row.get("symbol") or row.get("Symbol") or settings.SYMBOL).upper()
    type_str = row.get("type") or row.get("Type") or ""
    direction = _direction_from_type(type_str)

    lots = _to_float(row.get("lots") or row.get("Lots"))
    entry_price = _to_float(row.get("entry_price") or row.get("entry") or row.get("open"))
    exit_price = _to_float(row.get("exit_price") or row.get("exit") or row.get("close"))

    sl_pts = _to_float(row.get("sl_pts") or row.get("SL_pts"))
    tp_pts = _to_float(row.get("tp_pts") or row.get("TP_pts"))
    rr_eff = _to_float(row.get("rr_eff"))
    risk_trade = _to_float(row.get("risk_pct"))
    pnl_usd = _to_float(row.get("pnl_usd"))
    r_eff = _to_float(row.get("R_eff"))
    mfe_pts = _to_float(row.get("mfe_pts"))
    mae_pts = _to_float(row.get("mae_pts"))
    slippage_pts = _to_float(row.get("slippage_pts"))
    spread_pts = _to_float(row.get("spread_pts"))
    reason = (row.get("reason") or "").strip() or None

    # نستخدم R_eff كـ r_multiple (أقرب شيء)
    r_multiple = r_eff if r_eff is not None else rr_eff

    def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
        if not dt:
            return None
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    obj: Dict[str, Any] = {
        # لا يوجد Ticket في هذا الملف، نتركه None أو row_index
        "ticket": None,
        "symbol": symbol,
        "direction": direction,
        "volume": lots,
        "entry": entry_price,
        "exit": exit_price,
        "sl_pts": sl_pts,
        "tp_pts": tp_pts,
        "rr_eff": rr_eff,
        "pnl_usd": pnl_usd,
        "r_multiple": r_multiple,
        "mfe_pts": mfe_pts,
        "mae_pts": mae_pts,
        "slippage_pts": slippage_pts,
        "spread_pts": spread_pts,
        "ts": _dt_to_iso(ts_dt),
        "ts_open": _dt_to_iso(ts_dt),
        "ts_close": _dt_to_iso(ts_dt),
        "risk_pct_trade": risk_trade,
        "reason": reason,
        "row_index": dr.row_index,
    }

    # إضافة بيانات السياسة من live_config
    obj.update(
        policy_version=policy_meta.get("policy_version"),
        stability_mode=policy_meta.get("stability_mode"),
        rr=policy_meta.get("rr"),
        risk_pct=policy_meta.get("risk_pct"),
        ai_min_confidence=policy_meta.get("ai_min_confidence"),
    )

    return obj


def _append_jsonl(objs: List[Dict[str, Any]]) -> None:
    if not objs:
        return

    files_map: Dict[str, List[Dict[str, Any]]] = {}

    for obj in objs:
        ts_raw = obj.get("ts_close") or obj.get("ts_open") or obj.get("ts")
        dt = None
        if isinstance(ts_raw, str):
            try:
                dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                dt = None

        k = _month_key(dt)
        files_map.setdefault(k, []).append(obj)

    for month_key, month_objs in files_map.items():
        jsonl_path = LOGS_DIR / f"trades_{month_key}.jsonl"
        with jsonl_path.open("a", encoding="utf-8") as f:
            for o in month_objs:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        print(f"[sync_deals] wrote {len(month_objs)} trades to {jsonl_path}")


# =========================
# Entrypoint
# =========================

def main() -> None:
    _ensure_logs_dir()

    state = _load_state()
    last_row_index = int(state.get("last_row_index", 0) or 0)

    print(f"[sync_deals] deals_csv={DEALS_CSV_PATH}")
    print(f"[sync_deals] last_row_index={last_row_index}")

    new_deals = _load_new_deals(last_row_index)
    if not new_deals:
        print("[sync_deals] no new deals to sync.")
        return

    policy_meta = _load_live_policy_meta()
    objs: List[Dict[str, Any]] = []

    for dr in new_deals:
        try:
            o = _deal_to_jsonl_obj(dr, policy_meta=policy_meta)
            objs.append(o)
        except Exception as e:
            print(f"[sync_deals][WARN] failed to convert row_index={dr.row_index}: {e}")

    if not objs:
        print("[sync_deals] nothing converted, skip writing.")
        return

    _append_jsonl(objs)

    max_idx = max(dr.row_index for dr in new_deals)
    state["last_row_index"] = max_idx
    _save_state(state)

    print(f"[sync_deals] done. processed_rows={len(new_deals)} last_row_index={max_idx}")


if __name__ == "__main__":
    main()