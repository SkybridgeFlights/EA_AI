# -*- coding: utf-8 -*-signals
from __future__ import annotations

import glob
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.aggregator import generate_direction_confidence
from app.services.writer import write_ini_signal, resolve_ai_dir

router = APIRouter(tags=["signals"])

# =========================
# Schemas
# =========================
class GenerateReq(BaseModel):
    symbol: Optional[str] = None
    force: bool = Field(default=False)

class GenerateResp(BaseModel):
    symbol: str
    direction: str
    confidence: float
    rationale: str
    written_file: str
    ts: str

# =========================
# Helpers
# =========================
def _latest_ini_path(symbol: str | None = None) -> Optional[Path]:
    base = Path(resolve_ai_dir())
    cands: List[Path] = []
    if symbol:
        cands.append(base / f"{symbol.lower()}_signal.ini")
    cands += [Path(p) for p in glob.glob(str(base / "*.ini"))]
    cands = [p for p in cands if p.exists()]
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1]

def _parse_simple_ini(path: Path) -> Dict[str, Any]:
    """
    يقرأ INI بسيط key=value.
    يحاول UTF-8 ثم UTF-16 LE.
    """
    def _read(enc: str):
        with path.open("r", encoding=enc) as f:
            return f.readlines()

    try:
        lines = _read("utf-8")
    except UnicodeError:
        lines = _read("utf-16")

    out: Dict[str, Any] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out

# =========================
# Routes
# =========================
@router.post("/generate", response_model=GenerateResp)
def generate_signal(req: GenerateReq):
    symbol = (req.symbol or settings.SYMBOL).upper()
    direction, confidence, rationale = generate_direction_confidence(symbol, force=req.force)

    path = write_ini_signal(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        rationale=rationale,
        hold_minutes=settings.HOLD_MINUTES_DEFAULT,
        rr=settings.RR_DEFAULT,
        risk_pct=settings.RISK_PCT_DEFAULT,
        file_name=f"{symbol.lower()}_signal.ini",
    )

    return GenerateResp(
        symbol=symbol,
        direction=direction,
        confidence=float(confidence),
        rationale=rationale,
        written_file=str(path),
        ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

@router.get("/latest")
def latest_signal(symbol: Optional[str] = Query(None)):
    sym = (symbol or settings.SYMBOL).upper()
    p = _latest_ini_path(sym)
    if not p:
        raise HTTPException(status_code=404, detail="no_signal_files")

    meta = _parse_simple_ini(p)

    def _fnum(s: str, d: float = 0.0) -> float:
        try:
            return float(s)
        except Exception:
            return d

    return {
        "symbol": sym,
        "file": str(p),
        "direction": meta.get("direction", "").upper(),
        "confidence": _fnum(meta.get("confidence", "0")),
        "hold_minutes": int(_fnum(meta.get("hold_minutes", "0"))),
        "rr": _fnum(meta.get("rr", "0")),
        "risk_pct": _fnum(meta.get("risk_pct", "0")),
        "ts": meta.get("ts") or meta.get("time") or "",
        "reason": meta.get("rationale") or meta.get("reason") or "",
    }

@router.get("/list")
def list_signals():
    base = Path(resolve_ai_dir())
    files = [
        {"file": str(p), "size": p.stat().st_size, "mtime": int(p.stat().st_mtime)}
        for p in sorted(base.glob("*.ini"), key=lambda q: q.stat().st_mtime, reverse=True)
    ]
    return {"count": len(files), "files": files}

@router.get("/config")
def signal_config():
    return {
        "symbol": settings.SYMBOL,
        "ai_signals_dir": resolve_ai_dir(),
        "auto_write": {
            "enabled": getattr(settings, "AUTO_WRITE_ENABLED", False),
            "seconds": getattr(settings, "AUTO_WRITE_INTERVAL_SEC", 0),
            "force": getattr(settings, "AUTO_WRITE_FORCE", False),
        },
    }







