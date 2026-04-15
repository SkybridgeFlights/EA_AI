# -*- coding: utf-8 -*-signal_loop
from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Tuple, Optional

from app.config import settings
from app.services.aggregator import generate_direction_confidence
from app.services.writer import write_ini_signal, resolve_ai_dir


# معايير “تغير مادي”
_MIN_DELTA = 0.05     # فرق ثقة أدنى
_MIN_AGE_S = 60       # أقل عمر قبل السماح بإعادة الكتابة حتى لو لم يتغير شيء

_state_lock = threading.Lock()
_last: Tuple[str, float, float] | None = None  # (dir, conf, ts_epoch)


def _read_last(symbol: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    base = Path(resolve_ai_dir())
    p = base / f"{symbol.lower()}_signal.ini"
    if not p.exists():
        return None, None, None
    # يحاول UTF-16 ثم UTF-8
    try:
        text = p.read_text(encoding="utf-16")
    except UnicodeError:
        text = p.read_text(encoding="utf-8")
    meta = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if "=" in ln:
            k, v = ln.split("=", 1)
            meta[k.strip().lower()] = v.strip()
    try:
        direc = (meta.get("direction", "") or "").upper()
        conf = float(meta.get("confidence", "0") or 0)
        ts = float(meta.get("ts", "0") or 0)
        return direc, conf, ts
    except Exception:
        return None, None, None


def _should_write(new_dir: str, new_conf: float, symbol: str) -> bool:
    global _last
    with _state_lock:
        if _last is None:
            ld, lc, lt = _read_last(symbol)
            if ld is not None:
                _last = (ld, float(lc or 0.0), float(lt or 0.0))
        if _last is None:
            return True
        ld, lc, lt = _last
        age = time.time() - float(lt or 0.0)
        if new_dir != ld:
            return True
        if abs(new_conf - float(lc or 0.0)) >= _MIN_DELTA:
            return True
        if age >= _MIN_AGE_S:
            return True
        return False


def _persist(new_dir: str, new_conf: float):
    global _last
    _last = (new_dir, new_conf, time.time())


def run_loop(stop_event: threading.Event):
    sym = settings.SYMBOL
    interval = max(15, int(getattr(settings, "AUTO_WRITE_INTERVAL_SEC", 60)))
    while not stop_event.is_set():
        try:
            direction, confidence, rationale = generate_direction_confidence(sym, force=False)

            if _should_write(direction, confidence, sym):
                path = write_ini_signal(
                    symbol=sym,
                    direction=direction,
                    confidence=confidence,
                    rationale=rationale,
                    hold_minutes=int(getattr(settings, "HOLD_MINUTES_DEFAULT", 30)),
                    rr=float(getattr(settings, "RR_DEFAULT", 2.0)),
                    risk_pct=float(getattr(settings, "RISK_PCT_DEFAULT", 1.0)),
                    file_name=f"{sym.lower()}_signal.ini",
                )
                _persist(direction, confidence)
                print(f"[signals-auto] wrote {path} dir={direction} conf={confidence:.3f}")
            else:
                print("[signals-auto] skip write (no material change)")
        except Exception as e:
            print("[signals-auto][ERROR]", e)
        finally:
            stop_event.wait(interval)




