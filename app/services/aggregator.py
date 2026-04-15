# -*- coding: utf-8 -*-aggregator
from __future__ import annotations

import json
import math
import time
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from app.config import settings
from app.ml.model import predict_direction as model_predict


# =========================
# أدوات مساعدة
# =========================
def _upper(s: str) -> str:
    return (s or "").upper()


def _yf_symbol(symbol: str) -> str:
    s = _upper(symbol)
    if s == "XAUUSD" and getattr(settings, "USE_GC_F_FOR_XAU", False):
        return "GC=F"
    if len(s) == 6 and s.endswith("USD"):
        return s + "=X"
    return s


def _safe_hist(tkr: yf.Ticker, period: str, interval: str) -> pd.DataFrame:
    try:
        df = tkr.history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # اجبار أرقام عائمة
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.replace([np.inf, -np.inf], np.nan).dropna(how="any")
        return df
    except Exception:
        return pd.DataFrame()


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def _impact_to_num(imp: str) -> int:
    s = (imp or "").lower()
    if "high" in s or s == "3":
        return 3
    if "medium" in s or s == "2":
        return 2
    return 1


# =========================
# تقويم الأخبار (TradingEconomics)
# =========================
def get_news_phase_and_impact(
    now: datetime,
    minutes_pre: int,
    minutes_post: int,
    min_impact: int,
    ccys_csv: str,
) -> tuple[str, int]:
    url = "https://api.tradingeconomics.com/calendar"
    params = {}
    if getattr(settings, "TE_API_KEY", ""):
        params["c"] = settings.TE_API_KEY
        params["format"] = "json"
    try:
        r = requests.get(url, params=params, timeout=12)
        data = r.json()
    except Exception:
        return "none", 0

    allowed = [x.strip().upper() for x in (ccys_csv or "").split(",") if x.strip()]
    best_phase, best_imp = "none", 0

    for it in data[:200]:
        ccy = _upper(it.get("Currency"))
        if allowed and ccy not in allowed:
            continue
        impn = _impact_to_num(it.get("Importance", ""))
        if impn < min_impact:
            continue
        t_str = it.get("Date") or it.get("DateSpan") or ""
        try:
            evt = pd.to_datetime(t_str, utc=True).to_pydatetime()
        except Exception:
            continue
        start_block = evt - timedelta(minutes=minutes_pre)
        end_block = evt + timedelta(minutes=minutes_post)
        if start_block <= now <= end_block:
            phase = "pre" if now <= evt else "post"
            if impn > best_imp:
                best_imp = impn
                best_phase = phase

    return best_phase, best_imp


# =========================
# إشارة تقنية مستمرة دقيقة
# =========================
def _tech_score(symbol: str) -> tuple[int, float, str]:
    """
    نولد درجة مستمرة ∈ [-1,+1] من نافذتين 1m و5m مع RSI.
    نرجع:
      الاتجاه: {-1,0,+1}
      القوة: [0..1]
      تعليل مختصر
    """
    tkr = yf.Ticker(_yf_symbol(symbol))
    d1 = _safe_hist(tkr, period="1d", interval="1m")
    d5 = _safe_hist(tkr, period="5d", interval="5m")
    if d1.empty or d5.empty or len(d1) < 120 or len(d5) < 120:
        return 0, 0.0, "no-data"

    c1 = d1["Close"]
    c5 = d5["Close"]

    ema8, ema21 = _ema(c1, 8), _ema(c1, 21)
    rsi14 = _rsi(c1, 14)
    cross_up = float((ema8.iloc[-2] <= ema21.iloc[-2]) and (ema8.iloc[-1] > ema21.iloc[-1]))
    cross_dn = float((ema8.iloc[-2] >= ema21.iloc[-2]) and (ema8.iloc[-1] < ema21.iloc[-1]))
    bias1 = float(np.tanh((ema8.iloc[-1] - ema21.iloc[-1]) / (c1.iloc[-1] + 1e-12) * 50.0))
    rsi_bias = float((rsi14.iloc[-1] - 50.0) / 50.0)

    ema20, ema50 = _ema(c5, 20), _ema(c5, 50)
    bias5 = float(np.tanh((ema20.iloc[-1] - ema50.iloc[-1]) / (c5.iloc[-1] + 1e-12) * 25.0))

    raw = 0.45 * bias1 + 0.25 * rsi_bias + 0.30 * bias5 + 0.10 * (cross_up - cross_dn)
    raw = float(np.clip(raw, -1.0, 1.0))

    if abs(raw) < 0.05:
        return 0, 0.0, "flat"
    direction = 1 if raw > 0 else -1
    strength = float(min(1.0, abs(raw)))
    return direction, strength, f"ema(1m,5m)+rsi raw={raw:.3f}"


# =========================
# سنابشوت سعر
# =========================
def fetch_price_snapshot(symbol: str):
    tkr = yf.Ticker(_yf_symbol(symbol))
    info = _safe_hist(tkr, period="1d", interval="1m")
    if not info.empty:
        last = info.iloc[-1]
        return {"symbol": symbol, "price": float(last["Close"]), "ts": str(last.name)}
    return {"symbol": symbol, "price": None, "ts": None}


# =========================
# المزج النهائي ML + TECH + NEWS
# =========================
def generate_direction_confidence(symbol: str, force: bool = False):
    # TECH
    t_dir, t_strength, t_reason = _tech_score(symbol)

    # NEWS
    now = datetime.utcnow()
    phase, imp = get_news_phase_and_impact(
        now=now,
        minutes_pre=int(getattr(settings, "CAL_NO_TRADE_BEFORE", 5)),
        minutes_post=int(getattr(settings, "CAL_NO_TRADE_AFTER", 5)),
        min_impact=int(getattr(settings, "CAL_MIN_IMPACT", 2)),
        ccys_csv=(getattr(settings, "CAL_CURRENCIES", "USD") or "USD"),
    )

    # ML
    ml_dir, ml_conf, ml_reason = model_predict(symbol, phase, imp, t_dir, t_strength)

    # إذا ML واثق → نعتمد ML
    if ml_dir in ("BUY", "SELL"):
        rationale = f"ML:{ml_reason}; TECH:{t_reason}; NEWS:{phase}-{imp}"
        return ml_dir, float(min(max(ml_conf, 0.0), 0.99)), rationale

    # fallback ذكي عند غياب/اعتزال ML
    score = (t_strength if t_dir > 0 else (-t_strength if t_dir < 0 else 0.0))
    if phase == "pre" and imp >= 3:
        score *= 0.35
    if phase == "post" and imp >= 3 and t_dir != 0:
        score *= 1.15

    if score > 0.08:
        return "BUY", min(0.90, 0.55 + 0.40 * score), f"TECH:{t_reason}; NEWS:{phase}-{imp}"
    if score < -0.08:
        return "SELL", min(0.90, 0.55 + 0.40 * abs(score)), f"TECH:{t_reason}; NEWS:{phase}-{imp}"

    if force and t_dir != 0:
        return ("BUY" if t_dir > 0 else "SELL"), 0.35, f"FORCED:tech; TECH:{t_reason}; NEWS:{phase}-{imp}"
    return "FLAT", 0.0, f"TECH:{t_reason}; NEWS:{phase}-{imp}"








