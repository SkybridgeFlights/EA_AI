# app/core/live_schema.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict
import math


def _to_float(v: Any, default: float) -> float:
    if v is None:
        return float(default)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().strip('"').strip("'")
        if not s:
            return float(default)
        try:
            return float(s)
        except Exception:
            return float(default)
    return float(default)


def _to_int(v: Any, default: int) -> int:
    if v is None:
        return int(default)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v):
        return int(round(v))
    if isinstance(v, str):
        s = v.strip().strip('"').strip("'")
        if not s:
            return int(default)
        try:
            return int(round(float(s)))
        except Exception:
            return int(default)
    return int(default)


def _to_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off"):
            return False
    return bool(default)


def _clamp_float(v: float, lo: float, hi: float) -> float:
    if not math.isfinite(v):
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _clamp_int(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# حدود قوية للباراميترات الأساسية
_PARAM_BOUNDS = {
    # AI / Risk / RR
    "ai_min_confidence": (0.50, 0.99, 0.80),   # lo, hi, default
    "rr":               (1.10, 4.00, 2.00),
    "risk_pct":         (0.05, 2.00, 0.35),

    # TS / BE
    "ts_start":         (80,  2000, 280),
    "ts_step":          (10,  1000, 120),
    "be_trig":          (40,  1000, 120),
    "be_offs":          (0,   300,  30),

    # BE/TS المتقدمة
    "BE_MinR":               (0.10, 3.00, 0.30),
    "BE_MinGainPtsExtra":    (0,    500,  10),
    "BE_SpreadMul":          (0.5,  10.0, 1.8),
    "TS_MinDeltaModifyPts":  (1,    500,  25),
    "TS_CooldownBars":       (0,    200,  3),
    "MinSL_Gap_ATR":         (0.05, 5.00, 0.30),
    "MinSL_Gap_SprdMul":     (0.5,  10.0, 2.0),

    # حوكمة عامة
    "max_spread_pts":     (10,   5000, 350),
    "max_trades_per_day": (1,    500,  10),

    # أخبار
    "cal_no_trade_before_min": (0,  600, 5),
    "cal_no_trade_after_min":  (0,  600, 5),
    "cal_min_impact":          (1,  3,   2),
}

# مرايا legacy للأسماء التي يقرأها الـ EA مباشرة من قسم legacy
_LEGACY_KEYS = {
    "ai_min_confidence":       "AI_MinConfidence",
    "rr":                      "InpRR",
    "risk_pct":                "RiskPct",
    "ts_start":                "TS_Start",
    "ts_step":                 "TS_Step",
    "be_trig":                 "BE_Trig",
    "be_offs":                 "BE_Offs",
    "BE_MinR":                 "BE_MinR",
    "BE_MinGainPtsExtra":      "BE_MinGainPtsExtra",
    "BE_SpreadMul":            "BE_SpreadMul",
    "TS_MinDeltaModifyPts":    "TS_MinDeltaModifyPts",
    "TS_CooldownBars":         "TS_CooldownBars",
    "MinSL_Gap_ATR":           "MinSL_Gap_ATR",
    "MinSL_Gap_SprdMul":       "MinSL_Gap_SprdMul",
    "max_spread_pts":          "MaxSpreadPts",
    "max_trades_per_day":      "MaxTradesPerDay",
    "use_calendar":            "UseCalendar",
    "cal_no_trade_before_min": "Cal_NoTrade_BeforeMin",
    "cal_no_trade_after_min":  "Cal_NoTrade_AfterMin",
    "cal_min_impact":          "Cal_MinImpact",
}


def normalize_live_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    يطبّق:
    - schema_version ثابت
    - تنظيف/تحويل الأنواع للأرقام والـ bool
    - clamp قوي لكل الباراميترات الحساسة ضمن حدود منطقية
    - مزامنة policy.params مع legacy
    """
    if payload is None:
        payload = {}

    # ضمان تواجد الأقسام الأساسية
    root: Dict[str, Any] = dict(payload)
    root.setdefault("schema_version", "1.0")

    policy = dict(root.get("policy") or {})
    params = dict(policy.get("params") or {})
    legacy = dict(root.get("legacy") or {})

    # use_calendar بوصفه bool
    use_cal = _to_bool(params.get("use_calendar", True), True)
    params["use_calendar"] = use_cal

    # تطبيق حدود لكل param
    for name, (lo, hi, default) in _PARAM_BOUNDS.items():
        raw_val = params.get(name, default)

        # اختيار نوع الـ clamp بحسب نوع الـ default
        if isinstance(default, int) and not isinstance(default, bool):
            v_int = _to_int(raw_val, int(default))
            v_int = _clamp_int(v_int, int(lo), int(hi))
            params[name] = v_int
        else:
            v_f = _to_float(raw_val, float(default))
            v_f = _clamp_float(v_f, float(lo), float(hi))
            params[name] = v_f

    # إعادة كتابة use_calendar بشكل صريح في legacy أيضاً
    use_cal = _to_bool(params.get("use_calendar", True), True)
    params["use_calendar"] = use_cal

    # مزامنة param -> legacy بالأسماء المناسبة
    for p_name, l_name in _LEGACY_KEYS.items():
        if p_name == "use_calendar":
            legacy[l_name] = bool(use_cal)
            continue

        if p_name not in params:
            continue

        val = params[p_name]
        # نحترم نوع القيمة الذي يريده الـ EA في legacy:
        if isinstance(_PARAM_BOUNDS.get(p_name, (0, 0, 0))[2], int) and not isinstance(val, bool):
            legacy[l_name] = _to_int(val, int(_PARAM_BOUNDS[p_name][2]))
        else:
            legacy[l_name] = _to_float(val, float(_PARAM_BOUNDS[p_name][2]))

    # إعادة بناء الهياكل داخل الـ root
    policy["params"] = params
    root["policy"] = policy
    root["legacy"] = legacy

    return root
