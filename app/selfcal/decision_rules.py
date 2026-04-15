# -*- coding: utf-8 -*-decision_rules.py
"""
Self-Cal decision rules v1.1
- Inputs: execution Metrics computed from deals.csv (+ optional jsonl stats)
- Output: Policy object with bounded deltas and clear reasons
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple, List
import math

# ---------- Data models ----------

@dataclass
class Metrics:
    pf: float                 # profit factor
    wr: float                 # win rate %
    maxdd: float              # max absolute drawdown (deposit currency)
    trades: int               # total trades in window
    pnl_today: float          # PnL today
    slip_avg_pts: Optional[float] = None
    spread_med_pts: Optional[float] = None
    r_mean: Optional[float] = None
    r_std: Optional[float] = None

@dataclass
class Policy:
    # execution policy pushed to live_config.json
    ai_min_confidence: float = 0.80
    rr: float               = 2.0
    risk_pct: float         = 0.35
    ts_start: int           = 250
    ts_step: int            = 100
    be_trig: int            = 80
    be_offs: int            = 20
    max_spread_pts: int     = 350
    max_trades_per_day: int = 10
    use_calendar: bool      = True
    cal_no_trade_before_min: int = 5
    cal_no_trade_after_min: int  = 5
    cal_min_impact: int          = 2

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

# ---------- Helpers: bounds and deltas ----------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _limit_float(new_val: Optional[float], prev: float, frac: float,
                 hard_lo: float, hard_hi: float) -> Tuple[float, str]:
    """
    Limit |new-prev| <= frac*prev, and clamp to [hard_lo, hard_hi].
    If new_val is None use prev.
    """
    if new_val is None:
        return _clamp(prev, hard_lo, hard_hi), "kept"
    # prev may come as str from JSON
    try:
        p = float(prev)
    except Exception:
        p = float(hard_lo)
    lo = p * (1.0 - frac)
    hi = p * (1.0 + frac)
    bounded = _clamp(float(new_val), lo, hi)
    bounded = _clamp(bounded, hard_lo, hard_hi)
    return bounded, f"from {prev} -> {bounded}"

def _limit_int(new_val: Optional[int], prev: int, frac: float,
               hard_lo: int, hard_hi: int) -> Tuple[int, str]:
    if new_val is None:
        return int(_clamp(prev, hard_lo, hard_hi)), "kept"
    try:
        p = int(prev)
    except Exception:
        p = hard_lo
    lo = int(math.floor(p * (1.0 - frac)))
    hi = int(math.ceil (p * (1.0 + frac)))
    bounded = int(_clamp(int(new_val), lo, hi))
    bounded = int(_clamp(bounded, hard_lo, hard_hi))
    return bounded, f"from {prev} -> {bounded}"

def _limit_bool(new_val: Optional[bool], prev: bool) -> Tuple[bool, str]:
    if new_val is None:
        return bool(prev), "kept"
    return bool(new_val), f"from {prev} -> {bool(new_val)}"

# ---------- Core rule-set ----------

def decide(metrics: Metrics, prev_params: Optional[Dict[str, Any]]) -> Tuple[Policy, Dict[str, Any]]:
    """
    Map metrics -> new policy, then bound vs previous by ±20% and hard limits.
    prev_params may be None on first run.
    Returns (policy, explain_dict)
    """
    m = metrics
    prev = prev_params or {}
    reasons: List[str] = []

    # --- base proposal depending on performance buckets ---
    base = Policy()

    # Conservative defaults for low-data
    if m.trades < 500:
        base.ai_min_confidence = 0.85
        base.risk_pct = 0.25
        base.rr = 2.0
        base.ts_start, base.ts_step = 200, 80
        base.be_trig, base.be_offs = 60, 15
        reasons.append("low_data: trades<500 -> conservative")
    else:
        # Performance buckets
        if m.pf >= 1.25 and m.wr >= 52:
            # Good edge: allow slight more risk and tighter TS
            base.ai_min_confidence = 0.78
            base.risk_pct = 0.40
            base.rr = 2.2
            base.ts_start, base.ts_step = 220, 80
            base.be_trig, base.be_offs = 70, 15
            reasons.append("good_performance: pf>=1.25 & wr>=52")
        elif m.pf >= 1.05 and m.wr >= 48:
            # Neutral/mildly positive
            base.ai_min_confidence = 0.80
            base.risk_pct = 0.35
            base.rr = 2.0
            base.ts_start, base.ts_step = 240, 90
            base.be_trig, base.be_offs = 75, 18
            reasons.append("neutral: 1.05<=pf<1.25 & wr>=48")
        else:
            # Weak: raise confidence, cut risk, tighten management
            base.ai_min_confidence = 0.85
            base.risk_pct = 0.25
            base.rr = 1.9
            base.ts_start, base.ts_step = 200, 70
            base.be_trig, base.be_offs = 60, 15
            reasons.append("weak: pf<1.05 or wr<48")

    # Execution quality nudges
    if m.slip_avg_pts is not None and m.slip_avg_pts > 80:
        base.max_trades_per_day = 8
        reasons.append("high_slippage -> reduce max_trades_per_day")
    if m.spread_med_pts is not None:
        # Tighten max_spread if market spread small; relax a bit if wide
        if m.spread_med_pts <= 30:
            base.max_spread_pts = 200
            reasons.append("tight market spread -> max_spread=200")
        elif m.spread_med_pts >= 120:
            base.max_spread_pts = 400
            reasons.append("wide market spread -> max_spread=400")

    # Hard limits
    HARD = {
        "ai_min_confidence": (0.60, 0.95),
        "rr":                (1.5, 3.0),
        "risk_pct":          (0.20, 1.20),
        "ts_start":          (120, 400),
        "ts_step":           (40, 200),
        "be_trig":           (40, 120),
        "be_offs":           (5, 40),
        "max_spread_pts":    (120, 600),
        "max_trades_per_day":(5, 40),
    }
    DELTA_FRAC = 0.20  # ±20%

    # Previous values fallback from prev or base
    def _prev_num(key: str, default_val: float) -> float:
        v = prev.get(key, default_val)
        try:
            return float(v)
        except Exception:
            return float(default_val)

    def _prev_int(key: str, default_val: int) -> int:
        v = prev.get(key, default_val)
        try:
            return int(v)
        except Exception:
            return int(default_val)

    # Apply bounded deltas vs prev
    ai_min_confidence, r1 = _limit_float(base.ai_min_confidence,
                                         _prev_num("ai_min_confidence", base.ai_min_confidence),
                                         DELTA_FRAC, *HARD["ai_min_confidence"])
    rr,                  r2 = _limit_float(base.rr, _prev_num("rr", base.rr),
                                           DELTA_FRAC, *HARD["rr"])
    risk_pct,            r3 = _limit_float(base.risk_pct, _prev_num("risk_pct", base.risk_pct),
                                           DELTA_FRAC, *HARD["risk_pct"])
    ts_start,            r4 = _limit_int  (base.ts_start, _prev_int("ts_start", base.ts_start),
                                           DELTA_FRAC, *HARD["ts_start"])
    ts_step,             r5 = _limit_int  (base.ts_step, _prev_int("ts_step", base.ts_step),
                                           DELTA_FRAC, *HARD["ts_step"])
    be_trig,             r6 = _limit_int  (base.be_trig, _prev_int("be_trig", base.be_trig),
                                           DELTA_FRAC, *HARD["be_trig"])
    be_offs,             r7 = _limit_int  (base.be_offs, _prev_int("be_offs", base.be_offs),
                                           DELTA_FRAC, *HARD["be_offs"])
    max_spread_pts,      r8 = _limit_int  (base.max_spread_pts, _prev_int("max_spread_pts", base.max_spread_pts),
                                           DELTA_FRAC, *HARD["max_spread_pts"])
    max_trades_per_day,  r9 = _limit_int  (base.max_trades_per_day, _prev_int("max_trades_per_day", base.max_trades_per_day),
                                           DELTA_FRAC, *HARD["max_trades_per_day"])

    use_calendar, r10 = _limit_bool(base.use_calendar, bool(prev.get("use_calendar", base.use_calendar)))

    out = Policy(
        ai_min_confidence=ai_min_confidence,
        rr=rr,
        risk_pct=risk_pct,
        ts_start=ts_start,
        ts_step=ts_step,
        be_trig=be_trig,
        be_offs=be_offs,
        max_spread_pts=max_spread_pts,
        max_trades_per_day=max_trades_per_day,
        use_calendar=use_calendar,
        cal_no_trade_before_min=base.cal_no_trade_before_min,
        cal_no_trade_after_min=base.cal_no_trade_after_min,
        cal_min_impact=base.cal_min_impact,
    )

    explain = {
        "pf": m.pf, "wr": m.wr, "maxdd": m.maxdd, "trades": m.trades,
        "slip_avg_pts": m.slip_avg_pts, "spread_med_pts": m.spread_med_pts,
        "reasons": reasons,
        "bounded_deltas": {
            "ai_min_confidence": r1, "rr": r2, "risk_pct": r3, "ts_start": r4,
            "ts_step": r5, "be_trig": r6, "be_offs": r7, "max_spread_pts": r8,
            "max_trades_per_day": r9, "use_calendar": r10
        }
    }
    return out, explain







