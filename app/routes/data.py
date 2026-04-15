# data.py - Routes (Fixed)
from fastapi import APIRouter, Query
from typing import Optional
from app.services.aggregator import fetch_price_snapshot
from app.config import settings

router = APIRouter()


@router.get("/price")
def price(symbol: Optional[str] = None):
    sym = (symbol or settings.SYMBOL).upper()
    return fetch_price_snapshot(sym)


@router.get("/calendar")
def calendar(ccys: Optional[str] = None, limit: int = Query(20, ge=1, le=200)):
    from datetime import datetime
    from app.services.aggregator import get_news_phase_and_impact

    now = datetime.utcnow()
    currencies = ccys or settings.CAL_CURRENCIES or "USD"

    try:
        phase, impact = get_news_phase_and_impact(
            now=now,
            minutes_pre=int(getattr(settings, "CAL_NO_TRADE_BEFORE", 5)),
            minutes_post=int(getattr(settings, "CAL_NO_TRADE_AFTER", 5)),
            min_impact=int(getattr(settings, "CAL_MIN_IMPACT", 2)),
            ccys_csv=currencies,
        )
        return {
            "symbol": settings.SYMBOL,
            "currencies": currencies,
            "current_phase": phase,
            "current_impact": impact,
            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except Exception as e:
        return {
            "symbol": settings.SYMBOL,
            "currencies": currencies,
            "current_phase": "none",
            "current_impact": 0,
            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "error": str(e),
        }