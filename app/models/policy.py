# C:\EA_AI\app\models\policy.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ConfigScope(BaseModel):
    symbol: str = Field(..., description="رمز التداول مثل XAUUSD")
    tf: str = Field(..., description="الإطار الزمني مثل M15 أو H1")
    spread_bucket: Optional[str] = Field(None, description="low/mid/high")
    regime: Optional[str] = Field(None, description="trend/range/highvol/neutral")

    class Config:
        extra = "ignore"


class PolicyParams(BaseModel):
    # أساسي
    ai_min_confidence: float = Field(..., ge=0.0, le=1.0)
    rr: float = Field(..., gt=0.0)
    risk_pct: float = Field(..., gt=0.0)
    ts_start: int = Field(..., ge=0)
    ts_step: int = Field(..., ge=0)
    be_trig: int = Field(..., ge=0)
    be_offs: int = Field(..., ge=0)

    # حراس BE/TS الذكية
    BE_MinR: float = 0.30
    BE_MinGainPtsExtra: int = 10
    BE_SpreadMul: float = 1.8
    TS_MinDeltaModifyPts: int = 25
    TS_CooldownBars: int = 3
    MinSL_Gap_ATR: float = 0.30
    MinSL_Gap_SprdMul: float = 2.0

    # حدود/إعدادات إضافية
    max_spread_pts: int = 350
    max_trades_per_day: int = 10
    use_calendar: bool = True
    cal_no_trade_before_min: int = 5
    cal_no_trade_after_min: int = 5
    cal_min_impact: int = 2

    class Config:
        extra = "ignore"


class PolicyExplain(BaseModel):
    pf: float = 0.0
    wr: float = 0.0
    maxdd: float = 0.0
    trades: int = 0
    median_spread: Optional[float] = None
    regime: Optional[str] = None
    micro_guard: Optional[bool] = None
    rules: Optional[str] = None

    class Config:
        extra = "ignore"


class PolicyBody(BaseModel):
    version: str = "1.0"          # مضاف لتوافق قراءة body.version
    updated_at: str
    policy_version: str
    shadow: bool = False
    scope: ConfigScope
    params: PolicyParams
    explain: PolicyExplain
    checksum: Optional[str] = None

    class Config:
        extra = "ignore"



        