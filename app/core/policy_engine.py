# app/core/policy_engine.py

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.core.live_writer import write_policy
from app.analytics.metrics import compute_rolling_metrics, RollingWindowMetrics

# ========================= Regime Integration =======================

_REGIME_PARAMS: Dict[str, Dict[str, Any]] = {
    "STRONG_TREND": {"rr": 2.4,  "risk_pct": 1.0,  "ts_start": 200.0, "shadow": False},
    "WEAK_TREND":   {"rr": 2.0,  "risk_pct": 0.5,  "ts_start": 300.0, "shadow": False},
    "RANGE":        {"rr": 1.5,  "risk_pct": 0.25, "ts_start": 400.0, "shadow": False},
    "HIGH_VOL":     {"rr": 1.8,  "risk_pct": 0.0,  "ts_start": 280.0, "shadow": True},
}

_REGIME_STATE_PATH = Path(os.getenv(
    "REGIME_STATE_FILE",
    str(Path(__file__).resolve().parents[2] / "runtime" / "regime_state.json"),
))
_REGIME_MAX_AGE_HOURS = float(os.getenv("REGIME_MAX_AGE_HOURS", "4"))


def read_regime_state(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    يقرأ runtime/regime_state.json ويعيد البيانات إذا:
      - الملف موجود
      - الـ regime معروف (في _REGIME_PARAMS)
      - العمر أقل من REGIME_MAX_AGE_HOURS (افتراضي 4 ساعات)

    يعيد None إذا لم تتوفر بيانات صالحة.
    """
    p = _to_path(path) if path else _REGIME_STATE_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None

    regime = str(data.get("regime", "")).upper()
    if regime not in _REGIME_PARAMS:
        return None

    # تحقق من العمر
    updated_at_str = data.get("updated_at", "")
    if updated_at_str:
        try:
            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600
            if age_h > _REGIME_MAX_AGE_HOURS:
                return None  # بيانات قديمة
        except Exception:
            pass

    return data


def _apply_regime(payload: Dict[str, Any],
                  regime_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    يُطبّق إعدادات الـ Regime على الـ policy payload بعد normalize.
    يتجاوز Governor clamp لـ risk_pct=0.0 في وضع HIGH_VOL.
    """
    regime    = str(regime_info.get("regime", "")).upper()
    overrides = _REGIME_PARAMS.get(regime)
    if not overrides:
        return payload

    params = payload["policy"]["params"]
    legacy = payload.get("legacy", {})

    rr       = overrides.get("rr")
    risk_pct = overrides.get("risk_pct")
    ts_start = overrides.get("ts_start")
    shadow_r = bool(overrides.get("shadow", False))

    # تطبيق على params
    if rr is not None:
        params["rr"]      = float(rr)
        legacy["InpRR"]   = float(rr)
    if risk_pct is not None:
        params["risk_pct"]  = float(risk_pct)   # يتجاوز clamp عمداً لـ HIGH_VOL=0.0
        legacy["RiskPct"]   = float(risk_pct)
    if ts_start is not None:
        params["ts_start"]  = float(ts_start)
        legacy["TS_Start"]  = float(ts_start)

    # تسجيل الـ regime في params وexplain
    params["regime_override"] = regime
    explain = payload["policy"].get("explain", {})
    explain["regime_override"]    = regime
    explain["regime_confidence"]  = regime_info.get("confidence")
    explain["regime_bar_time"]    = regime_info.get("bar_time")
    payload["policy"]["explain"]  = explain

    # shadow mode لـ HIGH_VOL
    if shadow_r:
        payload["shadow"]          = True
        payload["policy"]["shadow"] = True
        legacy["shadow"]            = True

    payload["legacy"] = legacy
    return payload


# ========================= Helpers / Types ==========================


@dataclass
class CoreMetrics:
    pf: float = 0.0          # Profit Factor
    wr: float = 0.0          # Win Rate (0–1)
    maxdd: float = 0.0       # Max Drawdown (account currency)
    trades: int = 0          # Number of closed trades
    pnl_today: float = 0.0   # Placeholder, يمكن تحسينه لاحقًا


def _now_utc_iso() -> str:
    """Return current UTC time in ISO string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_policy_version() -> str:
    """Version key used in live_config.json."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _to_path(val: Any) -> Path:
    if isinstance(val, Path):
        return val
    return Path(str(val))


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except Exception:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ========================= Model Quality (from active_model.json) ====================


def _load_model_quality_from_file() -> Optional[Dict[str, float]]:
    """
    يقرأ ACTIVE_MODEL_FILE (عادة: C:\\EA_AI\\models\\active_model.json)
    ويحاول استخراج:
      - auc
      - acc (accuracy)
      - rows (عدد الصفوف في التدريب)
      - feats (عدد الميزات)

    مصمم ليكون مرنًا مع أي شكل معقول للـ JSON:
      - يمكن أن تكون القيم في الجذر
      - أو داخل block مثل "metrics" أو "train_metrics"
    """
    path_str = os.getenv("ACTIVE_MODEL_FILE", r"C:\EA_AI\models\active_model.json")
    p = Path(path_str)
    if not p.exists():
        return None

    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
        data = json.loads(text)
    except Exception:
        return None

    def _get_num(d: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in d:
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
        return None

    candidates = [data]
    # أضف أي بلوك داخلي اسمه metrics / train_metrics / eval / eval_metrics إذا وجد
    for k, v in list(data.items()):
        if isinstance(v, dict) and k.lower() in (
            "metrics",
            "train_metrics",
            "eval",
            "eval_metrics",
        ):
            candidates.append(v)

    auc: Optional[float] = None
    acc: Optional[float] = None
    rows: Optional[float] = None
    feats: Optional[float] = None

    for d in candidates:
        if auc is None:
            auc = _get_num(d, "auc", "AUC")
        if acc is None:
            acc = _get_num(d, "acc", "accuracy", "ACC")
        if rows is None:
            r = d.get("rows") or d.get("n_rows") or d.get("samples") or d.get("n_samples")
            if isinstance(r, (int, float)):
                rows = float(r)
        if feats is None:
            f = d.get("feats") or d.get("features") or d.get("n_features")
            if isinstance(f, (int, float)):
                feats = float(f)

    if auc is None and acc is None and rows is None:
        # لم نجد شيئًا مفيدًا
        return None

    return {
        "auc": float(auc) if auc is not None else 0.0,
        "acc": float(acc) if acc is not None else 0.0,
        "rows": float(rows) if rows is not None else 0.0,
        "feats": float(feats) if feats is not None else 0.0,
    }


# ========================= Core Metrics ============================


def compute_core_metrics(deals_csv_path: Path) -> CoreMetrics:
    """
    Compute basic core metrics from deals.csv.

    Expects a CSV with at least a 'profit' column (case-insensitive).
    If file is missing or malformed, returns zero metrics.
    """
    deals_csv_path = _to_path(deals_csv_path)
    if not deals_csv_path.exists():
        return CoreMetrics()

    try:
        with deals_csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return CoreMetrics()

            # find profit column (case-insensitive)
            profit_col: Optional[str] = None
            for col in reader.fieldnames:
                if col.lower() in ("profit", "pnl", "p&l", "pnl_usd"):
                    profit_col = col
                    break

            if profit_col is None:
                return CoreMetrics()

            trade_profits = []
            for row in reader:
                v = row.get(profit_col, "")
                if v is None or v == "":
                    continue
                try:
                    p = float(v)
                except Exception:
                    continue
                trade_profits.append(p)

            if not trade_profits:
                return CoreMetrics()

            trades = len(trade_profits)
            wins = sum(1 for p in trade_profits if p > 0.0)

            gross_profit = sum(p for p in trade_profits if p > 0.0)
            gross_loss = -sum(p for p in trade_profits if p < 0.0)
            if gross_loss > 0:
                pf = gross_profit / gross_loss
            else:
                # all wins أو لا يوجد خسائر -> PF كبير جدًا
                pf = 10.0

            wr = wins / trades if trades > 0 else 0.0

            # simple max drawdown on cumulative PnL
            cum = 0.0
            peak = 0.0
            maxdd = 0.0
            for p in trade_profits:
                cum += p
                if cum > peak:
                    peak = cum
                dd = peak - cum
                if dd > maxdd:
                    maxdd = dd

            return CoreMetrics(
                pf=float(pf),
                wr=float(wr),
                maxdd=float(maxdd),
                trades=trades,
                pnl_today=0.0,  # يمكن حسابها بالتاريخ لاحقًا
            )
    except Exception:
        # كن متحفظًا: إذا حدث أي خطأ، أرجع صفرات
        return CoreMetrics()


# ===================== Parameter Logic (Governor) ===================


@dataclass
class BaseDefaults:
    rr: float
    risk_pct: float
    ai_min_confidence: float
    ts_start: float
    ts_step: float
    be_trig: float
    be_offs: float
    be_min_r: float
    be_min_gain_extra: float
    be_spread_mul: float
    ts_min_delta_modify: float
    ts_cooldown_bars: int
    minsl_gap_atr: float
    minsl_gap_spread_mul: float
    max_spread_pts: int
    max_trades_per_day: int
    use_calendar: bool
    cal_no_trade_before_min: int
    cal_no_trade_after_min: int
    cal_min_impact: int


def build_base_defaults_from_env() -> BaseDefaults:
    """
    Balanced Adaptive defaults + AI-aware adjustment:

    - مخاطرة متوسطة
    - قيود سبريد مرنة
    - ثقة AI متوازنة (قابلة للتعديل حسب جودة النموذج)
    """
    # قيم مبدئية من .env
    default_rr = _env_float("RR_DEFAULT", 1.8)
    default_risk_pct = _env_float("RISK_PCT_DEFAULT", 0.30)
    default_ai_conf = _env_float("AI_MIN_CONF_DEFAULT", 0.65)

    # ملاحظة: MaxSpreadPts الافتراضي عالي حتى لا نمنع الصفقات على XAUUSD
    default_max_spread = int(_env_int("MAX_SPREAD_PTS_DEFAULT", 2000))
    default_max_trades = int(_env_int("MAX_TRADES_PER_DAY_DEFAULT", 2))

    # --- تعديل ذكي بناءً على جودة النموذج (active_model.json) ---
    mq = _load_model_quality_from_file()
    ai_conf = default_ai_conf
    risk_pct = default_risk_pct

    # لا نستخدم النموذج إلا إذا عندنا عيّنة معقولة
    if mq and mq.get("rows", 0.0) >= 300:
        auc = mq.get("auc", 0.0)
        # طبقات تقريبية حسب AUC
        if auc <= 0.55:
            # نموذج ضعيف -> كن دفاعيًا جدًا
            risk_pct *= 0.6
            ai_conf = max(ai_conf, 0.75)
        elif auc <= 0.65:
            # نموذج عادي أو دون المتوسط
            risk_pct *= 0.8
            ai_conf = max(ai_conf, 0.70)
        elif auc <= 0.75:
            # متوسط إلى جيد
            risk_pct *= 0.9
            ai_conf = max(ai_conf, 0.65)
        elif auc <= 0.85:
            # جيد جدًا
            risk_pct *= 1.05
            # يمكننا خفض العتبة قليلًا ليستفيد من النموذج
            ai_conf = min(ai_conf, 0.60)
        else:
            # AUC > 0.85 -> نموذج قوي جدًا (نادر، لكن نسمح بالقليل)
            risk_pct *= 1.10
            ai_conf = min(ai_conf, 0.58)

    # Clamp بعد التعديل
    risk_pct = _clamp(risk_pct, 0.10, 1.00)
    ai_conf = _clamp(ai_conf, 0.50, 0.90)

    return BaseDefaults(
        rr=default_rr,
        risk_pct=risk_pct,
        ai_min_confidence=ai_conf,
        ts_start=280.0,
        ts_step=120.0,
        be_trig=120.0,
        be_offs=30.0,
        be_min_r=0.3,
        be_min_gain_extra=10.0,
        be_spread_mul=1.8,
        ts_min_delta_modify=25.0,
        ts_cooldown_bars=3,
        minsl_gap_atr=0.3,
        minsl_gap_spread_mul=2.0,
        max_spread_pts=default_max_spread,
        max_trades_per_day=default_max_trades,
        use_calendar=True,
        cal_no_trade_before_min=_env_int("CAL_NO_TRADE_BEFORE", 5),
        cal_no_trade_after_min=_env_int("CAL_NO_TRADE_AFTER", 5),
        cal_min_impact=_env_int("CAL_MIN_IMPACT", 2),
    )


def adjust_params_from_metrics(
    metrics: CoreMetrics,
    base: BaseDefaults,
) -> Dict[str, Any]:
    """
    Balanced Adaptive Governor:

    - الهدف: NEVER SHUTDOWN (لا نجعل المخاطرة صفر تقريبًا)
    - فقط تعديل تدريجي:
        • أداء سيئ -> نقلل المخاطرة قليلاً ونرفع ثقة AI قليلاً
        • أداء جيد -> نسمح بزيادة بسيطة في المخاطرة
    - نحافظ دائمًا على:
        0.10% <= risk_pct <= 1.00%
        0.50 <= ai_min_confidence <= 0.90
    """
    rr = base.rr
    risk_pct = base.risk_pct
    ai_conf = base.ai_min_confidence

    # فترة بيانات صغيرة -> كن متوازنًا، لا قتالي ولا شديد الحذر
    if metrics.trades < 30:
        risk_pct *= 0.8  # تقليل بسيط
        ai_conf = max(ai_conf, 0.65)
    else:
        # أداء ضعيف جدًا
        if metrics.pf < 0.7 or metrics.wr < 0.30:
            risk_pct *= 0.5
            ai_conf = max(ai_conf, 0.75)

        # أداء متوسط/ضعيف
        elif metrics.pf < 1.0 or metrics.wr < 0.45:
            risk_pct *= 0.75
            ai_conf = max(ai_conf, 0.70)

        # أداء قوي
        elif metrics.pf > 1.5 and metrics.wr > 0.55:
            risk_pct *= 1.2  # زيادة بسيطة

        # غير ذلك -> نترك base كما هو

    # Clamp نهائي للـ risk و الـ AI confidence
    risk_pct = _clamp(risk_pct, 0.10, 1.00)
    ai_conf = _clamp(ai_conf, 0.50, 0.90)

    params = {
        "ai_min_confidence": float(ai_conf),
        "rr": float(rr),
        "risk_pct": float(risk_pct),
        "ts_start": float(base.ts_start),
        "ts_step": float(base.ts_step),
        "be_trig": float(base.be_trig),
        "be_offs": float(base.be_offs),
        "BE_MinR": float(base.be_min_r),
        "BE_MinGainPtsExtra": float(base.be_min_gain_extra),
        "BE_SpreadMul": float(base.be_spread_mul),
        "TS_MinDeltaModifyPts": float(base.ts_min_delta_modify),
        "TS_CooldownBars": int(base.ts_cooldown_bars),
        "MinSL_Gap_ATR": float(base.minsl_gap_atr),
        "MinSL_Gap_SprdMul": float(base.minsl_gap_spread_mul),
        "max_spread_pts": int(base.max_spread_pts),
        "max_trades_per_day": int(base.max_trades_per_day),
        "use_calendar": bool(base.use_calendar),
        "cal_no_trade_before_min": int(base.cal_no_trade_before_min),
        "cal_no_trade_after_min": int(base.cal_no_trade_after_min),
        "cal_min_impact": int(base.cal_min_impact),
    }

    return params


# ========================= Normalization Schema ====================


def normalize_live_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply a hard schema + clamps on the live payload before writing.
    Ensures:
      - All numeric values are in safe ranges.
      - Legacy block is consistent with policy.params.
      - Minimal required structure exists.
    """
    # Work on a shallow copy
    out = dict(payload)

    # ---------- Top-level skeleton ----------
    out.setdefault("schema_version", "1.0")
    out.setdefault("shadow", False)
    if "updated_at" not in out:
        out["updated_at"] = _now_utc_iso()
    if "policy_version" not in out:
        out["policy_version"] = _generate_policy_version()

    policy = dict(out.get("policy") or {})
    out["policy"] = policy

    policy.setdefault("version", "1.0")
    if "updated_at" not in policy:
        policy["updated_at"] = out["updated_at"]
    if "policy_version" not in policy:
        policy["policy_version"] = out["policy_version"]
    if "shadow" not in policy:
        policy["shadow"] = bool(out.get("shadow", False))

    scope = dict(policy.get("scope") or {})
    policy["scope"] = scope
    scope.setdefault("symbol", os.getenv("SYMBOL", "XAUUSD"))
    scope.setdefault("tf", "M15")
    scope.setdefault("spread_bucket", "mid")
    scope.setdefault("regime", "range")

    params = dict(policy.get("params") or {})
    policy["params"] = params

    # ---------- Clamp numeric params ----------
    def f(name: str, default: float, lo: float, hi: float, ndigits: int = 4) -> float:
        raw = params.get(name, default)
        try:
            val = float(raw)
        except Exception:
            val = default
        val = _clamp(val, lo, hi)
        return round(val, ndigits)

    def i(name: str, default: int, lo: int, hi: int) -> int:
        raw = params.get(name, default)
        try:
            val = int(raw)
        except Exception:
            val = default
        val = int(_clamp(float(val), float(lo), float(hi)))
        return val

    def b(name: str, default: bool = False) -> bool:
        raw = params.get(name, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")

    # Core risk/AI (Balanced clamps)
    params["ai_min_confidence"] = f("ai_min_confidence", 0.65, 0.50, 0.90, ndigits=3)
    params["rr"] = f("rr", 1.8, 1.0, 3.5, ndigits=2)
    params["risk_pct"] = f("risk_pct", 0.30, 0.10, 1.00, ndigits=3)

    # BE / TS / SL
    params["ts_start"] = f("ts_start", 280.0, 20.0, 5000.0, ndigits=1)
    params["ts_step"] = f("ts_step", 120.0, 10.0, 5000.0, ndigits=1)
    params["be_trig"] = f("be_trig", 120.0, 5.0, 5000.0, ndigits=1)
    params["be_offs"] = f("be_offs", 30.0, 0.0, 1000.0, ndigits=1)

    params["BE_MinR"] = f("BE_MinR", 0.3, 0.0, 10.0, ndigits=3)
    params["BE_MinGainPtsExtra"] = f("BE_MinGainPtsExtra", 10.0, 0.0, 5000.0, ndigits=2)
    params["BE_SpreadMul"] = f("BE_SpreadMul", 1.8, 0.5, 10.0, ndigits=3)

    params["TS_MinDeltaModifyPts"] = f("TS_MinDeltaModifyPts", 25.0, 1.0, 5000.0, ndigits=2)
    params["TS_CooldownBars"] = i("TS_CooldownBars", 3, 0, 1000)

    params["MinSL_Gap_ATR"] = f("MinSL_Gap_ATR", 0.3, 0.0, 10.0, ndigits=3)
    params["MinSL_Gap_SprdMul"] = f("MinSL_Gap_SprdMul", 2.0, 0.5, 50.0, ndigits=3)

    # Limits / Calendars
    # ملاحظة: نسمح بـ MaxSpreadPts كبير حتى لا نمنع الصفقات على XAUUSD
    params["max_spread_pts"] = i("max_spread_pts", 2000, 1, 10000)
    params["max_trades_per_day"] = i("max_trades_per_day", 2, 1, 2000)

    params["use_calendar"] = b("use_calendar", True)
    params["cal_no_trade_before_min"] = i("cal_no_trade_before_min", 5, 0, 240)
    params["cal_no_trade_after_min"] = i("cal_no_trade_after_min", 5, 0, 240)
    params["cal_min_impact"] = i("cal_min_impact", 2, 1, 3)

    policy["params"] = params

    # ---------- Core metrics ----------
    core_metrics = dict(out.get("core_metrics") or {})
    for k in ("pf", "wr", "maxdd", "trades", "pnl_today"):
        if k not in core_metrics:
            core_metrics[k] = 0.0 if k != "trades" else 0

    try:
        core_metrics["pf"] = float(core_metrics.get("pf", 0.0))
    except Exception:
        core_metrics["pf"] = 0.0
    try:
        core_metrics["wr"] = float(core_metrics.get("wr", 0.0))
    except Exception:
        core_metrics["wr"] = 0.0
    try:
        core_metrics["maxdd"] = float(core_metrics.get("maxdd", 0.0))
    except Exception:
        core_metrics["maxdd"] = 0.0
    try:
        core_metrics["trades"] = int(core_metrics.get("trades", 0))
    except Exception:
        core_metrics["trades"] = 0
    try:
        core_metrics["pnl_today"] = float(core_metrics.get("pnl_today", 0.0))
    except Exception:
        core_metrics["pnl_today"] = 0.0

    out["core_metrics"] = core_metrics

    # ---------- Explain block ----------
    explain = dict(policy.get("explain") or {})
    explain.setdefault("pf", core_metrics["pf"])
    explain.setdefault("wr", core_metrics["wr"])
    explain.setdefault("maxdd", core_metrics["maxdd"])
    explain.setdefault("trades", core_metrics["trades"])
    explain.setdefault("median_spread", None)
    explain.setdefault("regime", scope.get("regime", "range"))
    explain.setdefault("micro_guard", False)
    explain.setdefault("rules", None)

    # إضافة معلومات عن جودة النموذج (إن وجدت)
    try:
        mq = _load_model_quality_from_file()
    except Exception:
        mq = None
    if mq:
        explain.setdefault("ai_model_auc", mq.get("auc"))
        explain.setdefault("ai_model_acc", mq.get("acc"))
        explain.setdefault("ai_model_rows", mq.get("rows"))
        explain.setdefault("ai_model_feats", mq.get("feats"))

    # ===== NEW: دمج news_weights في explain (للاطلاع في الـ Dashboard) =====
    try:
        nw_raw = os.getenv("NEWS_WEIGHTS_PATH", "") or ""
        if nw_raw.strip():
            nw_path = Path(os.path.expandvars(os.path.expanduser(nw_raw.strip())))
        else:
            nw_path = Path(r"C:\EA_AI\runtime\news_weights.json")

        if nw_path.exists():
            try:
                text = nw_path.read_text(encoding="utf-8", errors="ignore")
                nw_data = json.loads(text)
                if "news_weights_updated_at" not in explain:
                    explain["news_weights_updated_at"] = nw_data.get("updated_at")
                if "news_weights" not in explain:
                    explain["news_weights"] = nw_data.get("weights")
            except Exception:
                # لا نكسر الـ normalize بسبب خطأ في ملف الأخبار
                pass
    except Exception:
        pass
    # =======================================================================

    policy["explain"] = explain
    out["policy"] = policy

    # ---------- Legacy Mapping (ما يقرؤه الـ EA مباشرة) ----------
    legacy: Dict[str, Any] = dict(out.get("legacy") or {})
    legacy["AI_MinConfidence"] = params["ai_min_confidence"]
    legacy["InpRR"] = params["rr"]
    legacy["RiskPct"] = params["risk_pct"]
    legacy["TS_Start"] = params["ts_start"]
    legacy["TS_Step"] = params["ts_step"]
    legacy["BE_Trig"] = params["be_trig"]
    legacy["BE_Offs"] = params["be_offs"]
    legacy["BE_MinR"] = params["BE_MinR"]
    legacy["BE_MinGainPtsExtra"] = params["BE_MinGainPtsExtra"]
    legacy["BE_SpreadMul"] = params["BE_SpreadMul"]
    legacy["TS_MinDeltaModifyPts"] = params["TS_MinDeltaModifyPts"]
    legacy["TS_CooldownBars"] = params["TS_CooldownBars"]
    legacy["MinSL_Gap_ATR"] = params["MinSL_Gap_ATR"]
    legacy["MinSL_Gap_SprdMul"] = params["MinSL_Gap_SprdMul"]
    legacy["MaxSpreadPts"] = params["max_spread_pts"]
    legacy["MaxTradesPerDay"] = params["max_trades_per_day"]
    legacy["UseCalendar"] = params["use_calendar"]
    legacy["Cal_NoTrade_BeforeMin"] = params["cal_no_trade_before_min"]
    legacy["Cal_NoTrade_AfterMin"] = params["cal_no_trade_after_min"]
    legacy["Cal_MinImpact"] = params["cal_min_impact"]

    out["legacy"] = legacy

    # Never touch _write_meta here; it will be added/updated by live_writer.
    return out


# ======================= Stability Mode Logic =======================


def classify_stability_mode(
    rolling: Dict[int, RollingWindowMetrics],
) -> str:
    """
    Balanced Stability classifier:

    يعيد واحدًا من:
      - "EARLY"       -> بيانات قليلة، نعاملها كـ NORMAL عمليًا
      - "NORMAL"      -> الأداء مقبول
      - "CONSERVATIVE" -> تقليل مخاطرة بسيط
      - "SAFE"        -> تقليل مخاطرة أكبر، لكن لا نوقف التداول
      - "CRISIS"      -> مخاطرة أقل، لكن لا = 0 (لا يوجد SHUTDOWN هنا)

    rolling: ناتج compute_rolling_metrics (dict: days -> RollingWindowMetrics)
    """
    m30 = rolling.get(30)
    m7 = rolling.get(7)

    if m30 is None or m30.trades < 30:
        return "EARLY"

    # أزمات حقيقية
    if (m30.pf is not None and m30.pf < 0.5) or (m30.max_dd_pct is not None and m30.max_dd_pct <= -40.0):
        return "CRISIS"

    # وضع آمن/دفاعي
    if (m30.pf is not None and m30.pf < 0.8) or (m30.max_dd_pct is not None and m30.max_dd_pct <= -25.0):
        return "SAFE"

    # وضع محافظ
    if (m30.pf is not None and m30.pf < 1.1) or (m30.max_dd_pct is not None and m30.max_dd_pct <= -15.0) or (
        m30.wr is not None and m30.wr < 40.0
    ):
        return "CONSERVATIVE"

    return "NORMAL"


# ============================ Build Policy ==========================


def build_policy(
    *,
    symbol: Optional[str] = None,
    tf: Optional[str] = None,
    deals_csv: Optional[Path] = None,
    shadow: bool = False,
    prev_path: Optional[Path] = None,
    **_: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    النسخة التي يستدعيها app.main (FastAPI):

    - يمكن أن تُستدعى هكذا:
        build_policy(shadow=..., prev_path=...)
      (كما في main.py الحالي)

    - أو مع تمرير symbol / tf / deals_csv يدويًا.

    ترجع:
      (policy_payload_normalized, core_metrics_dict)
    """
    # 1) resolve inputs / env
    if symbol is None:
        symbol = os.getenv("SYMBOL", "XAUUSD")
    if tf is None:
        tf = os.getenv("TF_DEFAULT", "M15")
    if deals_csv is None:
        deals_csv = os.getenv("DEALS_CSV_PATH", "")

    deals_csv_path = _to_path(deals_csv)

    # 2) core metrics + base defaults (AI-aware)
    core = compute_core_metrics(deals_csv_path)
    base = build_base_defaults_from_env()

    # 3) rolling metrics
    try:
        rolling = compute_rolling_metrics(deals_csv_path)
    except Exception:
        rolling = {}

    stability_mode = classify_stability_mode(rolling)

    # 4) تعديل base حسب stability_mode (Balanced Adaptive)
    if stability_mode == "CONSERVATIVE":
        base.risk_pct *= 0.8
        base.ai_min_confidence = max(base.ai_min_confidence, 0.70)
    elif stability_mode == "SAFE":
        base.risk_pct *= 0.6
        base.ai_min_confidence = max(base.ai_min_confidence, 0.75)
    elif stability_mode == "CRISIS":
        base.risk_pct *= 0.4
        base.ai_min_confidence = max(base.ai_min_confidence, 0.80)
    # EARLY / NORMAL -> نترك base كما هو

    # Clamp base بعد التعديلات
    base.risk_pct = _clamp(base.risk_pct, 0.10, 1.00)
    base.ai_min_confidence = _clamp(base.ai_min_confidence, 0.50, 0.90)

    # 5) governor params
    params = adjust_params_from_metrics(core, base)
    params["stability_mode"] = stability_mode

    # 6) explain / rolling details
    m7 = rolling.get(7)
    m30 = rolling.get(30)

    explain: Dict[str, Any] = {
        "pf": core.pf,
        "wr": core.wr,
        "maxdd": core.maxdd,
        "trades": core.trades,
        "pf_7d": m7.pf if m7 else None,
        "wr_7d": m7.wr if m7 else None,
        "maxdd_7d": m7.max_dd_pct if m7 else None,
        "pnl_7d": m7.pnl if m7 else None,
        "pf_30d": m30.pf if m30 else None,
        "wr_30d": m30.wr if m30 else None,
        "maxdd_30d": m30.max_dd_pct if m30 else None,
        "pnl_30d": m30.pnl if m30 else None,
        "stability_mode": stability_mode,
    }

    # إضافة معلومات جودة النموذج في explain (إن وُجدت)
    mq = _load_model_quality_from_file()
    if mq:
        explain["ai_model_auc"] = mq.get("auc")
        explain["ai_model_acc"] = mq.get("acc")
        explain["ai_model_rows"] = mq.get("rows")
        explain["ai_model_feats"] = mq.get("feats")

    # 7) scope
    scope: Dict[str, Any] = {
        "symbol": symbol,
        "tf": tf,
        "spread_bucket": "mid",
        "regime": "range",
    }

    # 8) timestamps
    updated_at = _now_utc_iso()
    policy_version = _generate_policy_version()

    core_dict = {
        "pf": core.pf,
        "wr": core.wr,
        "maxdd": core.maxdd,
        "trades": core.trades,
        "pnl_today": core.pnl_today,
    }

    payload_raw: Dict[str, Any] = {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "policy_version": policy_version,
        "shadow": bool(shadow),
        "policy": {
            "version": "1.0",
            "updated_at": updated_at,
            "policy_version": policy_version,
            "shadow": bool(shadow),
            "scope": scope,
            "params": params,
            "explain": explain,
        },
        "core_metrics": core_dict,
        # legacy سيتم توليده وتحديثه من normalize_live_payload
    }

    payload_norm = normalize_live_payload(payload_raw)
    return payload_norm, core_dict


# ============================ SelfCal API ===========================


def selfcal_once(
    symbol: str,
    tf: str,
    deals_csv: Path,
    decisions_csv: Optional[Path],
    jsonl_dir: Optional[Path],
    out_path: Path,
    mirror_path: Optional[Path] = None,
    shadow: bool = False,
) -> Dict[str, Any]:
    """
    نقطة الدخول الرئيسية لـ SelfCal (تُستخدم من tools.selfcal_runner):

    - تقرأ deals.csv
    - تبني policy عبر build_policy
    - تطبّق normalize_live_payload (داخل build_policy)
    - تكتب live_config.json عبر write_policy
    """
    out_path = _to_path(out_path)
    mirror_path_resolved = _to_path(mirror_path) if mirror_path else None

    policy_payload, core_dict = build_policy(
        symbol=symbol,
        tf=tf,
        deals_csv=deals_csv,
        shadow=shadow,
        prev_path=out_path,
    )

    # ── Regime override (من regime_state.json) ──────────────────────
    regime_info = read_regime_state()
    if regime_info:
        regime_name = regime_info.get("regime", "N/A")
        regime_conf = regime_info.get("confidence", 0.0)
        print(f"[policy] regime={regime_name} conf={regime_conf:.3f} "
              f"-> applying overrides")
        policy_payload = _apply_regime(policy_payload, regime_info)
    else:
        print("[policy] no valid regime_state — using Governor defaults")
    # ─────────────────────────────────────────────────────────────────

    result = write_policy(
        policy_payload,
        out_path=out_path,
        mirror_path=mirror_path_resolved,
        metrics_core=core_dict,
    )
    return result
