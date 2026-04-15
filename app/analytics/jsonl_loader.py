# app/analytics/jsonl_loader.py
"""
JSONL Loader for EA_AI project.

- يقرأ ملفات trades_YYYYMM.jsonl من مجلد JSONL_DIR.
- يدعم تعدد الرموز (XAUUSD, XAUUSDr, ...) عبر متغيّر البيئة TRAIN_SYMBOLS.
- يرجع DataFrame موحّد يحتوي على كل الحقول المتاحة.
"""

import os
import json
import glob
from typing import List, Optional, Sequence
from datetime import datetime

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # .env غير ضروري للتشغيل إذا كانت المتغيرات مضبوطة من النظام
    pass


def _parse_time(value) -> Optional[pd.Timestamp]:
    """
    يحاول تحويل حقل time (string أو number) إلى Timestamp.
    يقبل:
    - "2025-11-21 10:20:30"
    - ISO strings
    - أرقام epoch (ثواني)
    """
    if value is None:
        return None

    # إذا رقم (epoch)
    if isinstance(value, (int, float)):
        try:
            return pd.to_datetime(int(value), unit="s")
        except Exception:
            return None

    # إذا نص
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        # جرب as-is
        try:
            return pd.to_datetime(value)
        except Exception:
            pass

        # جرب تعديلات بسيطة على الفورمات
        for repl in [
            lambda s: s.replace("T", " ").replace("Z", ""),
            lambda s: s.replace("/", "-"),
        ]:
            try:
                return pd.to_datetime(repl(value))
            except Exception:
                continue

    return None


def _normalize_symbol(raw_sym: Optional[str]) -> Optional[str]:
    """
    توحيد شكل الرمز:
    - يحذف الفراغات
    - يحوّل للأحرف الكبيرة
    """
    if raw_sym is None:
        return None
    s = str(raw_sym).strip().upper()
    if not s:
        return None
    return s


def _load_env_symbols() -> List[str]:
    """
    يقرأ TRAIN_SYMBOLS من .env أو متغيرات النظام.

    مثال في .env:
        TRAIN_SYMBOLS=XAUUSD,XAUUSDr

    إذا لم يُضبط، نفترض: ["XAUUSD", "XAUUSDr"]
    """
    raw = os.getenv("TRAIN_SYMBOLS", "XAUUSD,XAUUSDr")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        parts = ["XAUUSD", "XAUUSDr"]
    # حوّل إلى upper
    return [p.upper() for p in parts]


def _iter_jsonl_objects(path: str):
    """
    يحاول قراءة ملف JSONL:
    - أولاً بـ UTF-8.
    - إذا حدث UnicodeDecodeError → يعيد المحاولة بـ latin-1 مع ignore.
    """
    # المحاولة الأولى: UTF-8
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    # سطر غير صالح JSON → نتجاهله
                    continue
        return
    except UnicodeDecodeError:
        pass  # سنحاول ترميزًا آخر

    # المحاولة الثانية: latin-1 مع ignore
    try:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        # لو الملف تالف تمامًا أو لا يمكن فتحه
        return


def load_jsonl_logs(
    jsonl_dir: Optional[str] = None,
    prefix: str = None,
    symbols: Optional[Sequence[str]] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    تحميل سجلات JSONL من مجلد معيّن.

    Parameters
    ----------
    jsonl_dir : str or None
        مجلد ملفات JSONL. إذا None → يأخذ JSONL_DIR أو TRADE_LOGS_DIR من .env.
    prefix : str or None
        بادئة أسماء الملفات (بدون امتداد). إذا None → يأخذ JSONL_FILE_PREFIX أو "trades_".
    symbols : list[str] or None
        قائمة الرموز المراد تضمينها (مثلاً ["XAUUSD","XAUUSDr"]).
        إذا None → تستخدم TRAIN_SYMBOLS من .env.
    start_time : datetime or None
        لا يتم تحميل سجلات قبل هذا الوقت (اختياري).
    end_time : datetime or None
        لا يتم تحميل سجلات بعد هذا الوقت (اختياري).

    Returns
    -------
    df : pandas.DataFrame
        يحتوي على كل السطور من كل الملفات المطابقة بعد الفلترة.
    """
    if jsonl_dir is None:
        jsonl_dir = (
            os.getenv("JSONL_DIR")
            or os.getenv("TRADE_LOGS_DIR")
            or "C:/EA_AI/runtime/logs"
        )

    if prefix is None:
        prefix = os.getenv("JSONL_FILE_PREFIX", "trades_")

    if symbols is None:
        symbols = _load_env_symbols()
    # طبّق upper على الرموز الداخلة
    symbols = [s.upper() for s in symbols]

    pattern = os.path.join(jsonl_dir, f"{prefix}*.jsonl")
    files = sorted(glob.glob(pattern))

    rows = []
    for path in files:
        # نستخدم الـ iterator الذي يتعامل مع الترميزات المختلفة
        for obj in _iter_jsonl_objects(path):
            # استخرج time + symbol
            raw_sym = obj.get("symbol") or obj.get("sym")
            sym_norm = _normalize_symbol(raw_sym)

            if sym_norm is None:
                # سجّل لكن بدون رمز: نحتفظ به فقط لو symbols فارغة (نادر)
                if symbols:
                    continue
            else:
                if symbols and sym_norm not in symbols:
                    continue

            ts = obj.get("time") or obj.get("ts")
            ts_parsed = _parse_time(ts)

            if start_time is not None and ts_parsed is not None:
                if ts_parsed < start_time:
                    continue

            if end_time is not None and ts_parsed is not None:
                if ts_parsed > end_time:
                    continue

            # أضف الحقول الأساسية
            obj["_time"] = ts_parsed
            obj["_symbol_norm"] = sym_norm

            rows.append(obj)

    if not rows:
        # DataFrame فارغ لكن ببعض الأعمدة القياسية
        return pd.DataFrame(
            columns=[
                "_time",
                "_symbol_norm",
                "symbol",
                "time",
                "R",
                "ai_conf_bucket",
                "atr_mult",
                "rr",
                "news_level",
                "mfe_pts",
                "mae_pts",
                "slippage_pts",
                "spread_open",
            ]
        )

    df = pd.DataFrame(rows)

    # تأكد من وجود الأعمدة القياسية حتى لو فارغة
    for col in [
        "_time",
        "_symbol_norm",
        "symbol",
        "time",
        "R",
        "ai_conf_bucket",
        "atr_mult",
        "rr",
        "news_level",
        "mfe_pts",
        "mae_pts",
        "slippage_pts",
        "spread_open",
    ]:
        if col not in df.columns:
            df[col] = None

    return df


if __name__ == "__main__":
    # اختبار بسيط عند التشغيل المباشر:
    df_test = load_jsonl_logs()
    print("Loaded rows:", len(df_test))
    print("Unique symbols:", df_test["_symbol_norm"].dropna().unique())
    print(df_test.head())
