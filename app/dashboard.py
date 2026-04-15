# ============================================
#  app/dashboard.py — EA Dashboard API (Deals + Decisions + JSONL)
# ============================================

from fastapi import APIRouter, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Any
from functools import lru_cache
import pandas as pd
import json, os, time, glob
import math  # مهم لتصفية NaN/inf

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(filename=".env"), override=True, encoding="utf-8")


# ---------- helpers: paths/env ----------
def _dequote(s: str) -> str:
    return s.strip().strip('"').strip("'")


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(_dequote(p))))


def _paths_from_env(key: str) -> List[Path]:
    raw = os.getenv(key, "") or ""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return [_expand(p) for p in parts]


DEALS_PATHS = _paths_from_env("DEALS_CSV_PATH")
DECISIONS_PATHS = _paths_from_env("DECISIONS_CSV_PATH")
JSONL_PATTERNS = [str(p) for p in _paths_from_env("JSONL_PATHS")]
_live_cfg_raw = os.getenv("LIVE_CONFIG_PATH", "").strip()
_mirror_live_cfg_raw = os.getenv("MIRROR_LIVE_CONFIG_TO", "").strip()
LIVE_CONFIG_PATH = _expand(_live_cfg_raw) if _live_cfg_raw else None
MIRROR_LIVE_CONFIG_TO = _expand(_mirror_live_cfg_raw) if _mirror_live_cfg_raw else None
ENV_SYMBOL = (os.getenv("SYMBOL") or "XAUUSD").upper()

_ROOT = Path(__file__).resolve().parents[1]
REGIME_STATE_PATH = Path(os.getenv(
    "REGIME_STATE_FILE",
    str(_ROOT / "runtime" / "regime_state.json"),
))
SELFCAL_LOG_PATH = _ROOT / "selfcal_log.txt"

router = APIRouter()


# ---------- sanitize floats for JSON ----------
def sanitize_floats(obj: Any) -> Any:
    """
    يحوّل NaN / +Inf / -Inf إلى None داخل dict / list / tuple / float
    حتى يصبح الكائن JSON compliant.
    """
    if isinstance(obj, float):
        if math.isfinite(obj):
            return obj
        return None

    if isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [sanitize_floats(v) for v in obj]

    return obj


# ---------- JSON helpers ----------
def _read_json(path: Optional[Path]) -> dict:
    if not path or not path.exists():
        return {}
    for enc in ("utf-8", "utf-16", "utf-16le", "utf-16be"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except Exception:
            continue
    return {}


def _write_json_atomic(path: Path, obj: dict):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = str(path) + ".tmp"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.replace(tmp, path)
    if MIRROR_LIVE_CONFIG_TO:
        try:
            tmp2 = str(MIRROR_LIVE_CONFIG_TO) + ".tmp"
            MIRROR_LIVE_CONFIG_TO.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp2, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            os.replace(tmp2, MIRROR_LIVE_CONFIG_TO)
        except Exception:
            pass


# ---------- CSV loading ----------
def _try_read_variants(path: Path, usecols: Optional[List[str]] = None):
    if not path.exists():
        return pd.DataFrame(), {"ok": False, "reason": "not_found"}

    encodings = ("utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be", "cp1256", "latin1")
    seps = [(None, "python"), (",", "c"), (";", "c"), ("\t", "c"), (r"\\t", "python")]
    decimals = (".", ",")

    errors = []
    for enc in encodings:
        for sep, engine in seps:
            for dec in decimals:
                try:
                    df = pd.read_csv(
                        path,
                        usecols=usecols,
                        sep=sep,
                        engine=engine,
                        decimal=dec,
                        encoding=enc,
                    )
                    if df is not None and df.shape[0] > 0 and df.shape[1] > 1:
                        return df, {
                            "ok": True,
                            "encoding": enc,
                            "sep": sep,
                            "engine": engine,
                            "decimal": dec,
                        }
                    else:
                        errors.append(f"enc={enc},sep={sep},eng={engine},dec={dec}: empty/1col")
                except Exception as e:
                    errors.append(f"enc={enc},sep={sep},eng={engine},dec={dec}: {type(e).__name__}")

    for enc in encodings:
        try:
            df = pd.read_csv(
                path,
                usecols=usecols,
                sep=r"\s+",
                engine="python",
                decimal=".",
                encoding=enc,
            )
            if df.shape[0] > 0 and df.shape[1] > 1:
                return df, {
                    "ok": True,
                    "encoding": enc,
                    "sep": r"\s+",
                    "engine": "python",
                    "decimal": ".",
                }
        except Exception as e:
            errors.append(f"fallback_ws enc={enc}: {type(e).__name__}")

    return pd.DataFrame(), {"ok": False, "reason": "all_failed", "errors": errors[:50]}


def _read_csv(path: Path, usecols: Optional[List[str]] = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()

    encodings = ("utf-8", "utf-16", "utf-16le", "utf-16be", "cp1256", "latin1")
    seps = [(None, "python"), (",", "c"), (";", "c"), ("\t", "c"), (r"\\t", "python")]
    decimals = (".", ",")

    for enc in encodings:
        for sep, engine in seps:
            for dec in decimals:
                try:
                    df = pd.read_csv(
                        path,
                        usecols=usecols,
                        sep=sep,
                        engine=engine,
                        decimal=dec,
                        encoding=enc,
                    )
                    if df is not None and df.shape[0] > 0 and df.shape[1] > 1:
                        df.columns = [
                            str(c).strip().lower().replace(" ", "_") for c in df.columns
                        ]
                        return df
                except Exception:
                    continue

    for enc in encodings:
        try:
            df = pd.read_csv(
                path,
                usecols=usecols,
                sep=r"\s+",
                engine="python",
                decimal=".",
                encoding=enc,
            )
            if df is not None and df.shape[0] > 0 and df.shape[1] > 1:
                df.columns = [
                    str(c).strip().lower().replace(" ", "_") for c in df.columns
                ]
                return df
        except Exception:
            continue

    # fallback: read as plain text and split on whitespace
    for enc in encodings:
        try:
            lines = path.read_text(encoding=enc, errors="ignore").splitlines()
            if not lines:
                continue
            header_parts = [p for p in lines[0].strip().split() if p]
            if len(header_parts) >= 2:
                rows = [
                    [p for p in ln.strip().split() if p] for ln in lines[1:] if ln.strip()
                ]
                if rows:
                    n = min(len(header_parts), len(rows[0]))
                    df = pd.DataFrame(
                        [r[:n] for r in rows],
                        columns=[h.lower() for h in header_parts[:n]],
                    )
                    df.columns = [
                        c.strip().lower().replace(" ", "_") for c in df.columns
                    ]
                    return df
        except Exception:
            continue

    return pd.DataFrame()


def _concat_existing(paths: List[Path], usecols: Optional[List[str]] = None) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for p in paths:
        try:
            df = _read_csv(p, usecols=usecols)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _mtime_signature(paths: List[Path]) -> Tuple[int, ...]:
    sig = []
    for p in paths:
        try:
            sig.append(int(p.stat().st_mtime))
        except FileNotFoundError:
            sig.append(0)
    return tuple(sig)


@lru_cache(maxsize=8)
def _read_deals_cached(sig: Tuple[int, ...]) -> pd.DataFrame:
    df = _concat_existing(DEALS_PATHS, usecols=None)
    if df.empty:
        return pd.DataFrame()

    df.columns = [str(c).strip().lower() for c in df.columns]
    rename_map = {
        "open": "price_open",
        "price": "price_open",  # دعم رأس "price" من الـEA الأخير
        "tp": "tp_pts",
        "sl": "sl_pts",
        "rr": "rr_eff",
        "risk": "risk_pct",
        "pnl_usd": "profit",  # من نسخة Common التفصيلية
        "entry_price": "price_open",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]

    need = [
        "ts",
        "symbol",
        "type",
        "lots",
        "price_open",
        "sl_pts",
        "tp_pts",
        "rr_eff",
        "risk_pct",
        "profit",
        "reason",
    ]
    for c in need:
        if c not in df.columns:
            df[c] = None

    with pd.option_context("mode.chained_assignment", None):
        if "ts" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["ts"]):
            try:
                df["ts"] = pd.to_datetime(
                    df["ts"],
                    format="%Y.%m.%d %H:%M:%S",
                    errors="coerce",
                    utc=True,
                )
            except Exception:
                df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)

        for c in [
            "lots",
            "price_open",
            "sl_pts",
            "tp_pts",
            "rr_eff",
            "risk_pct",
            "profit",
        ]:
            if c in df.columns:
                df[c] = (
                    df[c]
                    .astype(str)
                    .str.replace(",", ".", regex=False)
                    .replace({"None": None, "nan": None, "": None})
                )
                df[c] = pd.to_numeric(df[c], errors="coerce")

    try:
        if "ts" in df.columns:
            df = df.sort_values(by="ts", ascending=False, na_position="last")
        df = df.drop_duplicates().reset_index(drop=True)
    except Exception:
        pass

    return df


def _read_deals() -> pd.DataFrame:
    return _read_deals_cached(_mtime_signature(DEALS_PATHS))


def _read_decisions() -> pd.DataFrame:
    if not DECISIONS_PATHS:
        return pd.DataFrame()

    frames: List[pd.DataFrame] = []
    for p in DECISIONS_PATHS:
        try:
            df = _read_csv(p, usecols=None)
            if not df.empty:
                df.columns = [
                    str(c).strip().lower().replace(" ", "_") for c in df.columns
                ]
                for c in (
                    "ts",
                    "symbol",
                    "stage",
                    "final_dir",
                    "why",
                    "regime",
                    "ai_has",
                    "ai_dir",
                    "ai_conf",
                    "ai_reason",
                    "news_level",
                    "shadow",
                ):
                    if c not in df.columns:
                        df[c] = None

                if "ts" in df.columns and not pd.api.types.is_datetime64_any_dtype(
                    df["ts"]
                ):
                    try:
                        df["ts"] = pd.to_datetime(
                            df["ts"],
                            format="%Y.%m.%d %H:%M:%S",
                            errors="coerce",
                            utc=True,
                        )
                    except Exception:
                        df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)

                frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    try:
        if "ts" in out.columns:
            out = out.sort_values("ts", ascending=False, na_position="last")
        out = out.drop_duplicates().reset_index(drop=True)
    except Exception:
        pass

    return out


def _read_jsonl_trades(limit_per_file: int = 50000) -> pd.DataFrame:
    if not JSONL_PATTERNS:
        return pd.DataFrame()

    rows = []
    for pattern in JSONL_PATTERNS:
        for path in glob.glob(pattern):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i >= limit_per_file:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            rows.append(obj)
                        except Exception:
                            continue
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "time" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)

    return df.sort_values("time", ascending=False, na_position="last").reset_index(
        drop=True
    )


# ---------- analytics ----------
def _collect_symbols(df_deals: pd.DataFrame) -> List[str]:
    symbols: set[str] = set()
    if not df_deals.empty and "symbol" in df_deals.columns:
        for s in df_deals["symbol"].dropna().astype(str):
            s = s.strip().upper()
            if s:
                symbols.add(s)
    if ENV_SYMBOL and ENV_SYMBOL not in symbols:
        symbols.add(ENV_SYMBOL)
    return sorted(symbols)


def _make_equity_curve(df: pd.DataFrame, last_n: int = 200):
    if df.empty:
        return []
    prof = pd.to_numeric(df.get("profit", 0), errors="coerce").fillna(0.0)
    if prof.abs().sum() == 0 and "rr_eff" in df.columns:
        prof = pd.to_numeric(df["rr_eff"], errors="coerce").fillna(0.0)
    ser = prof.iloc[::-1].cumsum()
    ts = pd.to_datetime(df.iloc[::-1]["ts"], errors="coerce")
    out = [
        {"t": t.to_pydatetime().isoformat(), "y": float(y)}
        for t, y in zip(ts, ser)
        if pd.notna(t)
    ]
    return out[-last_n:] if len(out) > last_n else out


def _metrics_from_deals(df: pd.DataFrame):
    if df.empty:
        return {"trades": 0, "winrate": 0.0, "pf": 0.0, "maxdd": 0.0, "pnl_today": 0.0}

    prof = pd.to_numeric(df.get("profit", 0), errors="coerce").fillna(0.0)
    lots = pd.to_numeric(df.get("lots", 0), errors="coerce").fillna(0.0)

    trades = int(((lots > 0) | df.get("type", "").astype(str).str.len().gt(0)).sum())
    wins = int((prof > 0).sum())
    losses = int((prof < 0).sum())  # قد نستخدمه لاحقاً

    winrate = float(round(100.0 * wins / max(1, trades), 2))
    gross_win = float(prof[prof > 0].sum())
    gross_loss = float(-prof[prof < 0].sum())
    pf = 0.0 if gross_loss == 0 else float(round(gross_win / gross_loss, 2))

    eq = prof.iloc[::-1].cumsum().values.tolist()
    mdd, peak = 0.0, -1e18
    for v in eq:
        v = float(v)
        peak = max(peak, v)
        mdd = max(mdd, peak - v)

    today = pd.Timestamp.now(tz="UTC").normalize()
    pnl_today = float(prof[df["ts"] >= today].sum()) if "ts" in df.columns else 0.0

    return {
        "trades": trades,
        "winrate": winrate,
        "pf": pf,
        "maxdd": float(round(mdd, 2)),
        "pnl_today": float(round(pnl_today, 2)),
    }


# ---------- regime state ----------
def _read_regime_state() -> dict:
    return _read_json(REGIME_STATE_PATH)


# ---------- selfcal last timestamp ----------
def _selfcal_last_ts() -> Optional[str]:
    if not SELFCAL_LOG_PATH.exists():
        return None
    try:
        lines = SELFCAL_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            # format: [2026-04-01T05:00:02Z] [selfcal] OK ...
            if line.startswith("[") and "]" in line:
                return line[1:line.index("]")]
    except Exception:
        pass
    return None


def _trades_today(df: pd.DataFrame) -> int:
    if df.empty or "ts" not in df.columns:
        return 0
    try:
        today = pd.Timestamp.now(tz="UTC").normalize()
        return int((df["ts"] >= today).sum())
    except Exception:
        return 0


# ---------- selfcal health ----------
_health = {
    "last_run_ts": None,
    "next_run_eta_sec": None,
    "shadow": None,
    "last_ok": None,
    "last_err": None,
}


def selfcal_mark(
    *, ok: bool, shadow: Optional[bool], next_run_eta_sec: Optional[int], err: Optional[str] = None
):
    _health["last_run_ts"] = int(datetime.now(tz=timezone.utc).timestamp())
    _health["next_run_eta_sec"] = (
        max(0, int(next_run_eta_sec)) if next_run_eta_sec is not None else None
    )
    _health["shadow"] = False if shadow is None else bool(shadow)
    _health["last_ok"] = bool(ok)
    _health["last_err"] = err or None


# ---------- routes ----------
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    root = Path(__file__).resolve().parents[1]
    tpl = root / "app" / "templates" / "dashboard.html"
    return HTMLResponse(tpl.read_text(encoding="utf-8"))


@router.get("/api/dashboard/health")
def api_health():
    return JSONResponse({"selfcal": _health})


@router.get("/api/dashboard/status")
def api_status():
    """
    ملخص سريع: Regime + Policy + SelfCal + Deals
    مناسب لعرضه في أعلى الـ Dashboard كـ status bar.
    """
    # --- regime ---
    regime_raw = _read_regime_state()
    regime = {
        "name":       regime_raw.get("regime"),
        "confidence": regime_raw.get("confidence"),
        "updated_at": regime_raw.get("updated_at"),
        "bar_time":   regime_raw.get("bar_time"),
    } if regime_raw else None

    # --- policy (live_config) ---
    live_cfg = _read_json(LIVE_CONFIG_PATH) if LIVE_CONFIG_PATH else {}
    _pb = live_cfg.get("policy") or {}
    params = _pb.get("params") or live_cfg.get("params") or {}
    policy = {
        "rr":                 params.get("rr"),
        "risk_pct":           params.get("risk_pct"),
        "max_trades_per_day": params.get("max_trades_per_day"),
        "stability_mode":     params.get("stability_mode"),
        "regime_override":    params.get("regime_override"),
        "shadow":             live_cfg.get("shadow", False),
        "updated_at":         live_cfg.get("updated_at"),
    }

    # --- selfcal ---
    selfcal = {
        "last_ts":  _selfcal_last_ts(),
        "last_ok":  _health.get("last_ok"),
        "last_err": _health.get("last_err"),
    }

    # --- deals ---
    df = _read_deals()
    m = _metrics_from_deals(df)
    deals = {
        "pf":           m.get("pf"),
        "wr":           m.get("winrate"),
        "trades":       m.get("trades"),
        "trades_today": _trades_today(df),
        "pnl_today":    m.get("pnl_today"),
    }

    return JSONResponse(content=sanitize_floats({
        "now":    datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "policy": policy,
        "selfcal": selfcal,
        "deals":  deals,
    }))


@router.get("/api/dashboard/summary")
def api_summary(
    symbols: Optional[str] = Query(None),
    last_n: int = Query(200, ge=1, le=100000),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    search: str = Query(""),
    all: bool = Query(False),
):
    df = _read_deals()
    raw_rows = int(df.shape[0])  # عدد الصفقات الخام قبل أي فلترة

    symbols_all = _collect_symbols(df)

    if symbols:
        want = [
            s.strip().upper()
            for s in symbols.replace(";", ",").split(",")
            if s.strip()
        ]
        if not df.empty and "symbol" in df.columns:
            df = df[df["symbol"].astype(str).str.upper().isin(want)]

    df_win = df.copy() if all else (df.head(last_n).copy() if not df.empty else pd.DataFrame())

    if search and not df_win.empty:
        needle = search.lower()

        def _hit(row) -> bool:
            for col in ("symbol", "type", "reason"):
                val = str(row.get(col, "") or "")
                if needle in val.lower():
                    return True
            return False

        df_win = df_win[df_win.apply(_hit, axis=1)]

    total_rows = int(len(df_win))
    start, end = (page - 1) * page_size, (page - 1) * page_size + page_size
    df_page = df_win.iloc[start:end].copy() if total_rows > 0 else pd.DataFrame()

    metrics = _metrics_from_deals(df_win)
    equity = _make_equity_curve(
        df_win, last_n=last_n if not all else min(len(df_win), 10000)
    )

    live_cfg = _read_json(LIVE_CONFIG_PATH) if LIVE_CONFIG_PATH else {}
    _policy_block = live_cfg.get("policy") or {}
    params = (
        _policy_block.get("params")
        or live_cfg.get("params")
        or live_cfg.get("params_override")
        or {}
    )

    rows = []
    if not df_page.empty:
        for _, r in df_page.iterrows():
            ts = pd.to_datetime(r.get("ts"), errors="coerce")
            rows.append(
                {
                    "ts": ts.to_pydatetime().isoformat() if pd.notna(ts) else "",
                    "symbol": r.get("symbol", ""),
                    "type": r.get("type", ""),
                    "lots": float(r.get("lots", 0) or 0),
                    "price_open": float(r.get("price_open", 0) or 0),
                    "profit": float(r.get("profit", 0) or 0),
                    "reason": r.get("reason", ""),
                }
            )

    now = datetime.now(timezone.utc).isoformat()
    resp = {
        "now": now,
        "debug": {
            "raw_rows": raw_rows,
            "rows_after_filters": total_rows,
            "deals_paths": [str(p) for p in DEALS_PATHS],
        },
        "symbols": symbols_all,
        "metrics": metrics,
        "equity": equity,
        "last": rows,
        "total": total_rows,
        "page": page,
        "page_size": page_size,
        "params": {
            "rr":                 params.get("rr"),
            "risk_pct":           params.get("risk_pct"),
            "ai_min_confidence":  params.get("ai_min_confidence"),
            "max_trades_per_day": params.get("max_trades_per_day"),
            "max_spread_pts":     params.get("max_spread_pts"),
            "stability_mode":     params.get("stability_mode"),
            "regime_override":    params.get("regime_override"),
            "shadow":             live_cfg.get("shadow"),
            "freeze_until":       live_cfg.get("freeze_until"),
        }
        if params or live_cfg
        else {},
        "live_config_updated_at": (
            live_cfg.get("updated_at") or live_cfg.get("updatedAt")
        )
        if live_cfg
        else None,
        "shadow": live_cfg.get("shadow") if live_cfg else None,
        "data_sources": {
            "deals": [str(p) for p in DEALS_PATHS],
            "decisions": [str(p) for p in DECISIONS_PATHS],
            "jsonl": JSONL_PATTERNS,
            "live_config": str(LIVE_CONFIG_PATH) if LIVE_CONFIG_PATH else "",
            "existing_deals": [str(p) for p in DEALS_PATHS if Path(p).exists()],
        },
    }

    return JSONResponse(content=sanitize_floats(resp))


@router.get("/api/dashboard/decisions")
def api_decisions(
    symbols: Optional[str] = Query(None),
    last_n: int = Query(300, ge=1, le=20000),
    search: str = Query(""),
):
    df = _read_decisions()
    if df.empty:
        return JSONResponse(content={"rows": 0, "last": []})

    if symbols and "symbol" in df.columns:
        want = [
            s.strip().upper()
            for s in symbols.replace(";", ",").split(",")
            if s.strip()
        ]
        df = df[df["symbol"].astype(str).str.upper().isin(want)]

    if search:
        needle = search.lower()
        cols = ["symbol", "stage", "why", "ai_reason", "news_level"]

        df = df[
            df.apply(
                lambda r: any(
                    needle in str(r.get(c, "")).lower() for c in cols
                ),
                axis=1,
            )
        ]

    df = df.head(last_n).copy()
    rows = []
    for _, r in df.iterrows():
        ts = pd.to_datetime(r.get("ts"), errors="coerce")
        rows.append(
            {
                "ts": ts.to_pydatetime().isoformat() if pd.notna(ts) else "",
                "symbol": r.get("symbol", ""),
                "stage": r.get("stage", ""),
                "why": r.get("why", ""),
                "ai_dir": r.get("ai_dir", ""),
                "ai_conf": float(r.get("ai_conf", 0) or 0),
                "news": r.get("news_level", ""),
                "shadow": bool(r.get("shadow", False)),
            }
        )

    resp = {"rows": len(rows), "last": rows}
    return JSONResponse(content=sanitize_floats(resp))


@router.get("/api/dashboard/debug")
def api_debug():
    info = {
        "env_file": str(find_dotenv(filename=".env")),
        "env_loaded": True,
        "raw_env": {
            "DEALS_CSV_PATH": os.getenv("DEALS_CSV_PATH"),
            "DECISIONS_CSV_PATH": os.getenv("DECISIONS_CSV_PATH"),
            "JSONL_PATHS": os.getenv("JSONL_PATHS"),
            "LIVE_CONFIG_PATH": os.getenv("LIVE_CONFIG_PATH"),
            "MIRROR_LIVE_CONFIG_TO": os.getenv("MIRROR_LIVE_CONFIG_TO"),
            "SYMBOL": os.getenv("SYMBOL"),
        },
        "deals_paths": [str(p) for p in DEALS_PATHS],
        "decisions_paths": [str(p) for p in DECISIONS_PATHS],
        "jsonl_patterns": JSONL_PATTERNS,
        "existing_deals": [str(p) for p in DEALS_PATHS if p.exists()],
        "peek": {},
    }
    try:
        for p in DEALS_PATHS:
            if p.exists():
                info["peek"][str(p)] = Path(p).read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()[:3]
                break
    except Exception as e:
        info["peek_error"] = str(e)
    return JSONResponse(content=sanitize_floats(info))


@router.get("/api/dashboard/test_read")
def api_test_read():
    result = {}
    for p in DEALS_PATHS:
        if not p.exists():
            result[str(p)] = {"exists": False}
            continue
        df, meta = _try_read_variants(p, usecols=None)
        res = {
            "exists": True,
            "ok": meta.get("ok", False),
            "meta": meta,
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
        }
        if not df.empty:
            cols = [str(c).strip().lower() for c in df.columns]
            res["columns"] = cols[:40]
            try:
                res["head"] = df.head(2).to_dict(orient="records")
            except Exception:
                pass
        result[str(p)] = res
    return JSONResponse(content=sanitize_floats(result))


@router.post("/api/dashboard/toggle_shadow")
def toggle_shadow(payload: dict = Body(..., example={"shadow": True})):
    if not LIVE_CONFIG_PATH:
        return {"ok": False, "error": "LIVE_CONFIG_PATH not set"}
    desired = bool(payload.get("shadow", True))
    cfg = _read_json(LIVE_CONFIG_PATH) or {
        "version": 1,
        "updated_at": None,
        "params": {},
    }
    cfg["shadow"] = desired
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(LIVE_CONFIG_PATH, cfg)
    _health["shadow"] = desired
    return {"ok": True, "shadow": desired}


@router.post("/api/dashboard/freeze")
def freeze_minutes(payload: dict = Body(..., example={"minutes": 60})):
    if not LIVE_CONFIG_PATH:
        return {"ok": False, "error": "LIVE_CONFIG_PATH not set"}
    mins = int(payload.get("minutes", 60))
    mins = max(1, min(mins, 24 * 60))
    cfg = _read_json(LIVE_CONFIG_PATH) or {"version": 1, "params": {}}
    until = int(time.time()) + mins * 60
    cfg["freeze_until"] = until
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(LIVE_CONFIG_PATH, cfg)
    return {"ok": True, "freeze_until": until}


@router.post("/api/dashboard/live_config")
def save_live_config(payload: dict = Body(...)):
    if not LIVE_CONFIG_PATH:
        return {"ok": False, "error": "LIVE_CONFIG_PATH not set"}

    cfg = _read_json(LIVE_CONFIG_PATH) or {"version": 1, "params": {}}
    params = cfg.get("params") or {}

    def _pick(*keys, default=None):
        for k in keys:
            if k in payload and payload[k] is not None:
                return payload[k]
        return default

    upd = {
        "ai_min_confidence": _pick("ai_min_confidence", "AI_MinConfidence"),
        "inp_atr_sl_mult": _pick("InpATR_SL_Mult", "inp_atr_sl_mult"),
        "rr": _pick("InpRR", "rr"),
        "risk_pct": _pick("RiskPct", "risk_pct"),
        "news_filter_level": _pick("NewsFilterLevel", "news_filter_level"),
        "max_spread_pts": _pick("MaxSpreadPts", "max_spread_pts"),
    }

    for k, v in upd.items():
        if v is not None:
            params[k] = v

    cfg["params"] = params
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(LIVE_CONFIG_PATH, cfg)
    return {"ok": True, "params": params}
