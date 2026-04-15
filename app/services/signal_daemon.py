# -*- coding: utf-8 -*-
from __future__ import annotations
import os, time, traceback
from datetime import datetime, timezone
from app.config import settings
from app.services.aggregator import generate_direction_confidence
from app.services.writer import write_ini_signal

def _now_ts() -> int:
    return int(time.time())

def _env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name)
        return float(v) if v is not None else default
    except:
        return default

def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name)
        return int(v) if v is not None else default
    except:
        return default

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None: return default
    s = v.strip().lower()
    if s in ("1","true","yes","on","y"): return True
    if s in ("0","false","no","off","n"): return False
    return default

def main() -> None:
    symbol      = (getattr(settings, "SYMBOL", "XAUUSD") or "XAUUSD").upper()
    interval_s  = _env_int("SIGNAL_INTERVAL_SEC", 60)          # فترة التحديث
    force_write = _env_bool("SIGNAL_FORCE_WRITE", False)       # الكتابة حتى لو لم تتغير الإشارة
    hold_min    = _env_int("HOLD_MINUTES_DEFAULT", getattr(settings, "HOLD_MINUTES_DEFAULT", 30))
    rr_def      = _env_float("RR_DEFAULT", getattr(settings, "RR_DEFAULT", 2.0))
    risk_def    = _env_float("RISK_PCT_DEFAULT", getattr(settings, "RISK_PCT_DEFAULT", 1.0))

    last_payload = None
    print(f"[signal-daemon] start sym={symbol} interval={interval_s}s force={force_write}", flush=True)

    while True:
        try:
            direction, confidence, rationale = generate_direction_confidence(symbol, force=False)
            payload = (direction, round(confidence,6), rationale)

            should_write = force_write or (payload != last_payload)
            if should_write:
                path = write_ini_signal(
                    symbol=symbol,
                    direction=direction,
                    confidence=confidence,
                    rationale=rationale,
                    hold_minutes=hold_min,
                    rr=rr_def,
                    risk_pct=risk_def,
                    file_name=f"{symbol.lower()}_signal.ini",
                )
                last_payload = payload
                print(f"[signal-daemon] write ok file={path} dir={direction} conf={confidence:.3f} t={_now_ts()}", flush=True)
            else:
                print(f"[signal-daemon] no-change dir={direction} conf={confidence:.3f}", flush=True)
        except Exception as e:
            print("[signal-daemon][ERROR]\n" + traceback.format_exc(), flush=True)

        time.sleep(max(10, int(interval_s)))

if __name__ == "__main__":
    main()





