# -*- coding: utf-8 -*-extensions.py
from __future__ import annotations
import os, time
from pathlib import Path
from typing import Optional
from fastapi import Request, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi

# نقرأ المفتاح من متغيرات البيئة فقط لتفادي الاعتماد على settings
API_KEY = os.getenv("API_KEY", "")  # اتركه فارغاً لتعطيل الحماية

def _iso(t: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None

def _file_info(p: str) -> dict:
    try:
        st = Path(p).stat()
        return {"exists": True, "size": st.st_size, "mtime": _iso(st.st_mtime)}
    except Exception:
        return {"exists": False, "size": 0, "mtime": None}

def apply_extensions(app):
    """
    - يحمي /signals (POST/PUT/DELETE) و /admin عبر X-API-Key إذا كان API_KEY مضبوطاً.
    - يضيف /health.
    - يضيف /admin/reload-config.
    - يحقن SecurityScheme في OpenAPI ليظهر زر Authorize عند تفعيل المفتاح.
    """

    @app.middleware("http")
    async def _api_key_guard(request: Request, call_next):
        if API_KEY:
            path = request.url.path.rstrip("/")
            method = request.method.upper()
            needs_key = (path.startswith("/signals") and method in {"POST","PUT","DELETE"}) or path.startswith("/admin")
            if needs_key:
                key = request.headers.get("x-api-key") or request.headers.get("X-API-Key") or ""
                if key != API_KEY:
                    return JSONResponse(status_code=401, content={"detail": "invalid api key"})
        return await call_next(request)

    @app.get("/health")
    async def health():
        live_cfg = os.getenv("LIVE_CONFIG_PATH", "runtime/live_config.json")
        ai_dst   = os.path.join(os.environ.get("APPDATA",""), "MetaQuotes","Terminal","Common","Files","ai_signals","xauusd_signal.ini")
        ai_src   = os.getenv("AI_SIG_SRC", "")
        status = {
            "ok": True,
            "env": {
                "API_KEY_set": bool(API_KEY),
                "AUTO_WRITE_ENABLED": os.getenv("AUTO_WRITE_ENABLED", "1"),
                "AUTO_SELFCAL_ENABLED": os.getenv("AUTO_SELFCAL_ENABLED", "1"),
                "AI_SIG_SRC": ai_src,
                "AI_SIG_DST": ai_dst,
                "LIVE_CONFIG_PATH": live_cfg,
            },
            "files": {
                "live_config": _file_info(live_cfg),
                "ai_signal_dst": _file_info(ai_dst),
                "ai_signal_src": _file_info(ai_src) if ai_src else {"exists": False, "size": 0, "mtime": None},
            },
            "ts": _iso(time.time()),
        }
        return JSONResponse(status_code=200, content=status)

    @app.post("/admin/reload-config")
    async def reload_config(x_api_key: str = Header(default="")):
        if API_KEY and x_api_key != API_KEY:
            raise HTTPException(status_code=401, detail="invalid api key")
        out = {
            "AUTO_WRITE_ENABLED": os.getenv("AUTO_WRITE_ENABLED", "1"),
            "AUTO_SELFCAL_ENABLED": os.getenv("AUTO_SELFCAL_ENABLED", "1"),
        }
        return JSONResponse(status_code=200, content={"ok": True, **out})

    # حقن SecurityScheme في Swagger عندما يكون API_KEY مفعلاً
    if API_KEY:
        def custom_openapi():
            if app.openapi_schema:
                return app.openapi_schema
            schema = get_openapi(
                title=app.title, version=app.version,
                description=app.description, routes=app.routes
            )
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyAuth"] = {
                "type": "apiKey", "in": "header", "name": "X-API-Key"
            }
            schema["security"] = [{"ApiKeyAuth": []}]
            app.openapi_schema = schema
            return app.openapi_schema
        app.openapi = custom_openapi





        


        