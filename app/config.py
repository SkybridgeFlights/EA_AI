# app/config.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
from typing import List
from pydantic import BaseModel

# جذر المشروع C:\EA_AI (أو أي مسار آخر عندك)
ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _bool(env: str, default: bool = False) -> bool:
    """
    قراءة قيمة من الـ env وتحويلها لـ bool.
    """
    v = str(os.getenv(env, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _load_env_safe() -> None:
    """
    تحميل .env يدويًا:
    - ترميز UTF-8
    - تجاهل الأسطر الفارغة أو المعلّقة
    - تجاهل أي سطر مكسور بدون إظهار أخطاء
    - دعم قيم "" فارغة
    - لا نغطي على متغيرات ممروسة مسبقًا من الخارج (system env)
    """
    try:
        if not ENV_PATH.exists():
            return
        text = ENV_PATH.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            ln = line.strip()
            if not ln or ln.startswith("#"):
                continue
            if "=" not in ln:
                # سطر غير صالح → نتجاهله بلا ضجيج
                continue
            key, val = ln.split("=", 1)
            key = key.strip()
            val = val.strip()
            if not key:
                continue
            # إزالة علامات الاقتباس من البداية والنهاية إن وجدت
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            # لا نغطي على متغيرات ممروسة من الخارج
            os.environ.setdefault(key, val)
    except Exception:
        # أي خطأ هنا لا نسمح له بإيقاف السيرفر/الكاتب
        pass


# تحميل .env مرة واحدة عند استيراد الإعدادات
_load_env_safe()

# ------------------------------------------------------------------
# Defaults الذكية لمسارات MT5 / Common\Files
# ------------------------------------------------------------------
# مسار APPDATA القياسي للـ MT5 على ويندوز:
DEFAULT_MT5_COMMON = os.getenv(
    "MT5_COMMON_FILES",
    os.path.join(
        os.environ.get("APPDATA", ""),
        "MetaQuotes",
        "Terminal",
        "Common",
        "Files",
    ),
)

# لو لم تُحدد MT5_FILES_DIR → نستخدم نفس common\files
DEFAULT_MT5_FILES_DIR = os.getenv("MT5_FILES_DIR", DEFAULT_MT5_COMMON)

# live_config الافتراضي داخل Common\Files إلا إذا تم override في env
DEFAULT_LIVE_CONFIG = os.getenv(
    "LIVE_CONFIG_PATH",
    os.path.join(DEFAULT_MT5_COMMON, "live_config.json"),
)

# المرآة الافتراضية → نفس الملف إلا إذا تم override في env
DEFAULT_MIRROR_LIVE = os.getenv("MIRROR_LIVE_CONFIG_TO", DEFAULT_LIVE_CONFIG)

# deals.csv الافتراضي داخل Common\Files إلا إذا تم override في env
DEFAULT_DEALS_CSV = os.getenv(
    "DEALS_CSV_PATH",
    os.path.join(DEFAULT_MT5_COMMON, "deals.csv"),
)

# JSONL الافتراضي داخل runtime\logs إلا إذا تم override في env
DEFAULT_JSONL_DIR = os.getenv("JSONL_DIR", r"C:\EA_AI\runtime\logs")

# جذور بيانات أخرى افتراضية
DEFAULT_FEATURE_STORE_ROOT = os.getenv("FEATURE_STORE_ROOT", r"C:\EA_AI\data")
DEFAULT_OHLC_ROOT = os.getenv("OHLC_ROOT", r"C:\EA_AI\data\ohlc")
DEFAULT_CALENDAR_ROOT = os.getenv("CALENDAR_ROOT", r"C:\EA_AI\data\calendar")
DEFAULT_TRADE_LOGS_DIR = os.getenv("TRADE_LOGS_DIR", r"C:\EA_AI\runtime\logs")
DEFAULT_ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", r"C:\EA_AI\artifacts")
DEFAULT_MODEL_DIR = os.getenv("MODEL_DIR", r"C:\EA_AI\models")
DEFAULT_ACTIVE_MODEL_FILE = os.getenv(
    "ACTIVE_MODEL_FILE", r"C:\EA_AI\models\active_model.json"
)

# AI signals dir الافتراضي داخل Common\Files/ai_signals إلا إذا تم override في env
DEFAULT_AI_SIGNALS_DIR = os.getenv(
    "AI_SIGNALS_DIR",
    os.path.join(DEFAULT_MT5_COMMON, "ai_signals"),
)

# Best result path الافتراضي
DEFAULT_BEST_RESULT_PATH = os.getenv(
    "BEST_RESULT_PATH", r"C:\EA_AI\artifacts\best_result.json"
)


class Settings(BaseModel):
    # ===== مفاتيح API =====
    TE_API_KEY: str = os.getenv("TE_API_KEY", "")
    FMP_API_KEY: str = os.getenv("FMP_API_KEY", "")
    ALPHA_VANTAGE_KEY: str = os.getenv("ALPHA_VANTAGE_KEY", "")

    # ===== الرمز =====
    SYMBOL: str = os.getenv("SYMBOL", "XAUUSD")
    USE_GC_F_FOR_XAU: bool = _bool("USE_GC_F_FOR_XAU", True)

    # ===== مسارات MT5 / Common\Files =====
    MT5_COMMON_FILES: str = DEFAULT_MT5_COMMON
    MT5_FILES_DIR: str = DEFAULT_MT5_FILES_DIR

    # ===== live_config =====
    LIVE_CONFIG_PATH: str = DEFAULT_LIVE_CONFIG
    MIRROR_LIVE_CONFIG_TO: str = DEFAULT_MIRROR_LIVE

    # ===== deals / JSONL / signals =====
    DEALS_CSV_PATH: str = DEFAULT_DEALS_CSV
    JSONL_DIR: str = DEFAULT_JSONL_DIR
    JSONL_FILE_PREFIX: str = os.getenv("JSONL_FILE_PREFIX", "trades_")
    AI_SIGNALS_DIR: str = DEFAULT_AI_SIGNALS_DIR

    # ===== جذور الـ data =====
    FEATURE_STORE_ROOT: str = DEFAULT_FEATURE_STORE_ROOT
    OHLC_ROOT: str = DEFAULT_OHLC_ROOT
    CALENDAR_ROOT: str = DEFAULT_CALENDAR_ROOT

    # ===== Logs / Artifacts =====
    TRADE_LOGS_DIR: str = DEFAULT_TRADE_LOGS_DIR
    ARTIFACTS_DIR: str = DEFAULT_ARTIFACTS_DIR
    BEST_RESULT_PATH: str = DEFAULT_BEST_RESULT_PATH

    # ===== نافذة الأخبار =====
    CAL_NO_TRADE_BEFORE: int = int(os.getenv("CAL_NO_TRADE_BEFORE", "5"))
    CAL_NO_TRADE_AFTER: int = int(os.getenv("CAL_NO_TRADE_AFTER", "5"))
    CAL_MIN_IMPACT: int = int(os.getenv("CAL_MIN_IMPACT", "2"))
    CAL_CURRENCIES: str = os.getenv("CAL_CURRENCIES", "")

    # ===== النماذج والتدريب =====
    MODEL_DIR: str = DEFAULT_MODEL_DIR
    ACTIVE_MODEL_FILE: str = DEFAULT_ACTIVE_MODEL_FILE
    TRAIN_LOOKBACK_DAYS: int = int(os.getenv("TRAIN_LOOKBACK_DAYS", "365"))
    TRAIN_HORIZON: int = int(os.getenv("TRAIN_HORIZON", "6"))

    # ===== SelfCal =====
    AUTO_SELFCAL_ENABLED: bool = _bool("AUTO_SELFCAL_ENABLED", True)
    AUTO_SELFCAL_INTERVAL_SEC: int = int(
        os.getenv("AUTO_SELFCAL_INTERVAL_SEC", "900")
    )
    SELFCAL_SHADOW: bool = _bool("SELFCAL_SHADOW", False)

    # ===== كاتب الإشارة (writer) =====
    AUTO_WRITE_ENABLED: bool = _bool("AUTO_WRITE_ENABLED", True)
    AUTO_WRITE_INTERVAL_SEC: int = int(os.getenv("AUTO_WRITE_INTERVAL_SEC", "60"))
    AUTO_WRITE_FORCE: bool = _bool("AUTO_WRITE_FORCE", False)

    # ===== القيم الافتراضية للإشارة =====
    HOLD_MINUTES_DEFAULT: int = int(os.getenv("HOLD_MINUTES_DEFAULT", "30"))
    RR_DEFAULT: float = float(os.getenv("RR_DEFAULT", "2.0"))
    RISK_PCT_DEFAULT: float = float(os.getenv("RISK_PCT_DEFAULT", "1.0"))

    # back-compat (من أجل أي كود قديم يستخدم SIGNAL_INTERVAL_MIN)
    SIGNAL_INTERVAL_MIN: int = max(
        1, int(int(os.getenv("AUTO_WRITE_INTERVAL_SEC", "60")) // 60)
    )

    # ===== CORS / Scope (للتوافق مع settings.py القديم) =====
    CORS_ALLOW_ORIGINS: List[str] = ["*"]
    SCOPE_SYMBOL: str = os.getenv("SCOPE_SYMBOL", os.getenv("SYMBOL", "XAUUSD"))
    SCOPE_TF: str = os.getenv("SCOPE_TF", "M15")

    # ===== Alerts (اختياري) =====
    ALERTS_ENABLED: bool = _bool("ALERTS_ENABLED", False)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")


settings = Settings()

















