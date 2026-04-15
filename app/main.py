# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.policy_engine import selfcal_once
from app.analytics.metrics import (
    rolling_metrics,
    what_if,
    compute_rolling_metrics,  # نستخدمه في /api/metrics/core
)
from app.selfcal.reporting import read_last_lines

# مسارات مهمة
LIVE_CONFIG_PATH = Path(getattr(settings, "LIVE_CONFIG_PATH", "runtime/live_config.json")).resolve()
SELFCAL_STATE_PATH = LIVE_CONFIG_PATH.parent / "selfcal_state.json"

app = FastAPI(title="EA_AI Backend", version="2.0.0")

# ================== CORS ==================
app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, "CORS_ALLOW_ORIGINS", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ——— ربط الراوترات الموجودة لديك ———
# إشارات
try:
    from app.routes import signals as _signals_routes
    app.include_router(_signals_routes.router, prefix="/signals")
except Exception:
    pass

# بيانات / تدريب
try:
    from app.routes import data as _data_routes
    app.include_router(_data_routes.router, prefix="/data")
except Exception:
    pass

try:
    from app.routes import train as _train_routes
    app.include_router(_train_routes.router, prefix="/train")
except Exception:
    pass

# الداشبورد الأصلية لديك
_dashboard_bound = False
try:
    from app.dashboard import router as dashboard_router  # type: ignore

    app.include_router(dashboard_router, prefix="")
    _dashboard_bound = True
except Exception:
    pass

if not _dashboard_bound:
    for candidate in ("dashboard_app", "app"):
        try:
            from app import dashboard as _dash  # type: ignore

            sub = getattr(_dash, candidate, None)
            if sub is not None:
                app.mount("/dashboard", sub)
                _dashboard_bound = True
                break
        except Exception:
            pass

# ——— مسارات/مجلدات ———
TRADE_LOGS_DIR = Path(settings.TRADE_LOGS_DIR)
AI_SIGNALS_DIR = Path(settings.AI_SIGNALS_DIR)
MODEL_DIR = Path(settings.MODEL_DIR)
LIVE_CONFIG_PATH = Path(settings.LIVE_CONFIG_PATH)
BEST_RESULT_PATH = Path(getattr(settings, "BEST_RESULT_PATH", r"C:\EA_AI\artifacts\best_result.json"))
MIRROR_LIVE = getattr(settings, "MIRROR_LIVE_CONFIG_TO", "")

for p in (TRADE_LOGS_DIR, AI_SIGNALS_DIR, MODEL_DIR, LIVE_CONFIG_PATH.parent, BEST_RESULT_PATH.parent):
    p.mkdir(parents=True, exist_ok=True)

# كتابة live_config الفارغ مرة أولى + المرآة
try:
    if not LIVE_CONFIG_PATH.exists():
        LIVE_CONFIG_PATH.write_text("{}", encoding="utf-8")
    if MIRROR_LIVE:
        from shutil import copy2

        tmp = MIRROR_LIVE + ".tmp"
        Path(MIRROR_LIVE).parent.mkdir(parents=True, exist_ok=True)
        copy2(LIVE_CONFIG_PATH, tmp)
        os.replace(tmp, MIRROR_LIVE)
        print(f"[boot-mirror] {LIVE_CONFIG_PATH} -> {MIRROR_LIVE}", flush=True)
except Exception as _e:
    print("[boot-mirror][ERR]", _e, flush=True)

# ——— أعلام ———
AUTO_SELFCAL_ENABLED = bool(getattr(settings, "AUTO_SELFCAL_ENABLED", True))
AUTO_SELFCAL_INTERVAL_SEC = int(getattr(settings, "AUTO_SELFCAL_INTERVAL_SEC", 900))
SELFCAL_SHADOW = bool(getattr(settings, "SELFCAL_SHADOW", True))

# ——— صحة ———
START_TS = time.time()
_last_selfcal_ts: Optional[float] = None
_last_selfcal_err: Optional[str] = None

# ——— Root / Health ———
@app.get("/")
def root():
    return {
        "status": "ok",
        "dashboard_bound": _dashboard_bound,
        "symbol": settings.SYMBOL,
        "ai_signals_dir": str(AI_SIGNALS_DIR),
        "live_config_path": str(LIVE_CONFIG_PATH),
        "mirror_live_config_to": MIRROR_LIVE,
        "auto_selfcal": AUTO_SELFCAL_ENABLED,
        "selfcal_interval_sec": AUTO_SELFCAL_INTERVAL_SEC,
    }


@app.get("/healthz")
def healthz():
    return {
        "uptime_sec": round(time.time() - START_TS, 1),
        "last_selfcal_ts": _last_selfcal_ts,
        "last_selfcal_err": _last_selfcal_err,
        "shadow": SELFCAL_SHADOW,
        "auto": AUTO_SELFCAL_ENABLED,
    }


# ——— حلقة Self-Cal في الخلفية ———
_bg_stop = threading.Event()
_selfcal_thread: Optional[threading.Thread] = None


def _selfcal_loop():
    global _last_selfcal_ts, _last_selfcal_err
    if not AUTO_SELFCAL_ENABLED:
        print("[selfcal] disabled", flush=True)
        return

    print("[selfcal] started", flush=True)
    sec = int(max(30, AUTO_SELFCAL_INTERVAL_SEC))

    while not _bg_stop.is_set():
        try:
            # نقرأ المسارات من الـ env لتتناسب مع أي تعريف لـ selfcal_once
            deals_csv = os.getenv("DEALS_CSV_PATH", "")
            decisions_csv = os.getenv("DECISIONS_CSV_PATH", "")
            jsonl_dir = os.getenv("JSONL_DIR", "C:/EA_AI/runtime/logs")

            payload = selfcal_once(
                symbol=getattr(settings, "SYMBOL", os.getenv("SYMBOL", "XAUUSD")),
                tf="M15",
                deals_csv=deals_csv,
                decisions_csv=decisions_csv,
                jsonl_dir=jsonl_dir,
                out_path=LIVE_CONFIG_PATH,
                mirror_path=MIRROR_LIVE,
                shadow=SELFCAL_SHADOW,
            )

            _last_selfcal_ts = time.time()
            _last_selfcal_err = None

            # كتابة حالة selfcal_state.json للاطلاع من /api/selfcal/health
            try:
                state = {
                    "ok": True,
                    "updated_at": payload.get("updated_at"),
                    "policy_version": payload.get("policy_version"),
                    "shadow": payload.get("shadow", SELFCAL_SHADOW),
                    "core_metrics": payload.get("core_metrics", {}),
                    "scope": (payload.get("policy") or {}).get("scope", {}),
                    "params": (payload.get("policy") or {}).get("params", {}),
                }
                SELFCAL_STATE_PATH.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                # لا نكسر الحلقة بسبب خطأ في كتابة ملف الحالة
                pass

            policy = payload.get("policy") or {}
            params = policy.get("params") or {}
            meta = payload.get("_write_meta") or {}

            print(
                "[selfcal] wrote live_config.json "
                f"ver={payload.get('policy_version')} shadow={payload.get('shadow')} "
                f"rr={params.get('rr')} risk={params.get('risk_pct')} "
                f"checksum={meta.get('checksum')}",
                flush=True,
            )
        except Exception:
            _last_selfcal_err = traceback.format_exc()
            print("[selfcal][ERROR]\n" + _last_selfcal_err, flush=True)
        finally:
            _bg_stop.wait(timeout=sec)


# ——— APIs ———
@app.post("/api/selfcal/once")
def api_selfcal_once(shadow: bool = Query(False)):
    deals_csv = os.getenv("DEALS_CSV_PATH", "")
    decisions_csv = os.getenv("DECISIONS_CSV_PATH", "")
    jsonl_dir = os.getenv("JSONL_DIR", "C:/EA_AI/runtime/logs")

    payload = selfcal_once(
        symbol=getattr(settings, "SYMBOL", os.getenv("SYMBOL", "XAUUSD")),
        tf="M15",
        deals_csv=deals_csv,
        decisions_csv=decisions_csv,
        jsonl_dir=jsonl_dir,
        out_path=LIVE_CONFIG_PATH,
        mirror_path=MIRROR_LIVE,
        shadow=bool(shadow),
    )
    return {
        "ok": True,
        "payload": payload,
        "path": str(LIVE_CONFIG_PATH),
        "mirror": MIRROR_LIVE,
    }


@app.get("/api/selfcal/rolling")
def api_selfcal_rolling(days: int = Query(7, ge=1, le=90)):
    return rolling_metrics(days=days)


@app.post("/api/selfcal/whatif")
def api_selfcal_whatif(
    rr: Optional[float] = None,
    risk_pct: Optional[float] = None,
    ts_start: Optional[int] = None,
    ts_step: Optional[int] = None,
    be_trig: Optional[int] = None,
    be_offs: Optional[int] = None,
):
    return what_if(
        rr=rr,
        risk_pct=risk_pct,
        ts_start=ts_start,
        ts_step=ts_step,
        be_trig=be_trig,
        be_offs=be_offs,
    )


@app.get("/api/selfcal/live_config")
def api_selfcal_live_config():
    """
    يعرض محتوى live_config.json الحالي كما يراه الإكسبرت.
    """
    if not LIVE_CONFIG_PATH.exists():
        return {
            "ok": False,
            "reason": "not_found",
            "path": str(LIVE_CONFIG_PATH),
        }

    try:
        data = json.loads(LIVE_CONFIG_PATH.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "live_config": data,
            "path": str(LIVE_CONFIG_PATH),
        }
    except Exception as e:
        return {
            "ok": False,
            "reason": "read_error",
            "error": str(e),
            "path": str(LIVE_CONFIG_PATH),
        }


@app.get("/api/selfcal/health")
def api_selfcal_health():
    """
    Health للـ SelfCal:
    - آخر تحديث
    - رقم نسخة السياسة
    - الـ scope الحالي
    - الـ core_metrics الأخيرة
    """
    if not SELFCAL_STATE_PATH.exists():
        return {
            "ok": False,
            "reason": "state_not_found",
            "path": str(SELFCAL_STATE_PATH),
        }

    try:
        data = json.loads(SELFCAL_STATE_PATH.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        return {
            "ok": False,
            "reason": "state_read_error",
            "error": str(e),
            "path": str(SELFCAL_STATE_PATH),
        }


@app.get("/api/selfcal/last_report")
def api_selfcal_last_report():
    p = Path(os.getenv("ARTIFACTS_DIR", "artifacts")) / "policy_reports"
    if not p.exists():
        return {"ok": False, "reason": "no_dir"}
    files = sorted([x for x in p.glob("policy_report_*.jsonl")])
    if not files:
        return {"ok": False, "reason": "no_report"}
    last = str(files[-1])
    lines = read_last_lines(last, n=1)
    if not lines:
        return {"ok": False, "reason": "empty_report", "file": last}
    try:
        entry = json.loads(lines[-1])
    except Exception:
        return {"ok": False, "reason": "parse_error", "file": last}
    return {"ok": True, "file": last, "entry": entry}


# ============ NEW: Core metrics + AI model endpoint ===============
@app.get("/api/metrics/core")
def api_metrics_core(days: int = Query(30, ge=1, le=365)):
    """
    Endpoint مراقبة موحّد:

    يعيد:
      - Rolling metrics من deals.csv لنافذة days
      - آخر params من live_config.json (rr, risk_pct, ai_min_confidence, stability_mode, ...)
      - AI model metrics (auc, acc, rows, feats) إن وُجدت في policy.explain
    """
    # ---- Rolling metrics من deals.csv ----
    deals_path_str = os.getenv("DEALS_CSV_PATH", "")
    deals_path = Path(deals_path_str) if deals_path_str else None

    rolling = None
    if deals_path and deals_path.exists():
        try:
            mm = compute_rolling_metrics(deals_path, windows=(days,))
            m = mm.get(days)
            if m:
                rolling = {
                    "window_days": m.window_days,
                    "trades": m.trades,
                    "pf": m.pf,
                    "wr": m.wr,
                    "max_dd_pct": m.max_dd_pct,
                    "pnl": m.pnl,
                }
        except Exception as e:
            rolling = {"error": str(e)}

    if rolling is None:
        rolling = {
            "window_days": days,
            "trades": 0,
            "pf": 0.0,
            "wr": 0.0,
            "max_dd_pct": 0.0,
            "pnl": 0.0,
        }

    # ---- قراءة live_config.json لاستخراج params + model metrics ----
    live: dict = {}
    ai_model: dict = {}
    try:
        if LIVE_CONFIG_PATH.exists():
            cfg = json.loads(LIVE_CONFIG_PATH.read_text(encoding="utf-8"))
            policy = cfg.get("policy") or {}
            params = policy.get("params") or {}
            explain = policy.get("explain") or {}

            live = {
                "rr": params.get("rr"),
                "risk_pct": params.get("risk_pct"),
                "ai_min_confidence": params.get("ai_min_confidence"),
                "max_spread_pts": params.get("max_spread_pts"),
                "max_trades_per_day": params.get("max_trades_per_day"),
                "use_calendar": params.get("use_calendar"),
                "stability_mode": params.get("stability_mode") or explain.get("stability_mode"),
                "updated_at": cfg.get("updated_at") or policy.get("updated_at"),
                "policy_version": cfg.get("policy_version") or policy.get("policy_version"),
                "shadow": cfg.get("shadow", policy.get("shadow")),
            }

            ai_model = {
                "auc": explain.get("ai_model_auc"),
                "acc": explain.get("ai_model_acc"),
                "rows": explain.get("ai_model_rows"),
                "feats": explain.get("ai_model_feats"),
            }
    except Exception as e:
        live = {"error": str(e)}

    return {
        "rolling": rolling,
        "live": live,
        "ai_model": ai_model,
    }
# ================================================================


@app.on_event("startup")
def on_startup():
    global _selfcal_thread
    _bg_stop.clear()
    if _selfcal_thread is None:
        _selfcal_thread = threading.Thread(target=_selfcal_loop, name="selfcal", daemon=True)
        _selfcal_thread.start()


@app.on_event("shutdown")
def on_shutdown():
    _bg_stop.set()
    print("[selfcal] stopped.", flush=True)