# app/models/live_config.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field
except ImportError:  # احتياط لو كنت على v1
    from pydantic import BaseModel, Field


class LiveScope(BaseModel):
    symbol: str
    tf: str
    spread_bucket: Optional[str] = None
    regime: Optional[str] = None


class LiveParams(BaseModel):
    # نفس ما نستخدمه في PolicyParams
    ai_min_confidence: float
    rr: float
    risk_pct: float

    ts_start: int
    ts_step: int
    be_trig: int
    be_offs: int

    BE_MinR: float
    BE_MinGainPtsExtra: int
    BE_SpreadMul: float
    TS_MinDeltaModifyPts: int
    TS_CooldownBars: int
    MinSL_Gap_ATR: float
    MinSL_Gap_SprdMul: float

    max_spread_pts: int
    max_trades_per_day: int

    use_calendar: bool
    cal_no_trade_before_min: int
    cal_no_trade_after_min: int
    cal_min_impact: int


class LiveExplain(BaseModel):
    pf: float
    wr: float
    maxdd: float
    trades: int
    median_spread: Optional[float] = None
    regime: Optional[str] = None
    micro_guard: Optional[bool] = None


class LivePolicy(BaseModel):
    """
    هذا هو الـ PolicyBody كما يكتب إلى JSON.
    نتركه مرن قليلاً لكن مع الحقول الأساسية.
    """
    updated_at: datetime
    policy_version: str
    shadow: bool = False

    scope: LiveScope
    params: LiveParams
    explain: LiveExplain

    checksum: str


class LiveConfigPayload(BaseModel):
    """
    الشكل النهائي لـ live_config.json
    """
    schema_version: str = Field("1.0", description="نسخة سكيمـا live_config")
    updated_at: datetime
    policy_version: str
    shadow: bool = False

    policy: LivePolicy
    core_metrics: Dict[str, Any] = Field(default_factory=dict)
    legacy: Dict[str, Any] = Field(default_factory=dict)








