# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import time
import random
import json
from pathlib import Path
from typing import Dict, Tuple

# =========================
# Settings
# =========================
try:
    from app.config import settings
    _AI_DIR = getattr(settings, "AI_SIGNALS_DIR", None)
except Exception:
    settings = None
    _AI_DIR = None

# =========================
# Paths
# =========================
def _mt5_common_files() -> str:
    base = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files")
    os.makedirs(base, exist_ok=True)
    return base


def resolve_ai_dir() -> str:
    d = _AI_DIR or os.path.join(os.getcwd(), "ai_signals")
    os.makedirs(d, exist_ok=True)
    return d


def _ensure_dir(d: str) -> str:
    os.makedirs(d, exist_ok=True)
    return d


# =========================
# Atomic writes + encodings
# =========================
def _to_crlf(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\r\n")


def _atomic_write_bytes(path: str, data: bytes, retries: int = 20, sleep_sec: float = 0.05) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for _ in range(retries):
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return
        except (PermissionError, FileNotFoundError):
            time.sleep(sleep_sec)
    raise RuntimeError(f"atomic write failed: {path}")


def _write_utf16le_bom_crlf_atomic(path: str, text: str) -> None:
    data = _to_crlf(text).encode("utf-16-le")
    _atomic_write_bytes(path, b"\xff\xfe" + data)


# =========================
# News weights helpers
# =========================

def _news_weights_path() -> Path:
    """
    يحدد مسار ملف أوزان الأخبار:
      - NEWS_WEIGHTS_PATH من env إذا وُجد
      - وإلا C:\\EA_AI\\runtime\\news_weights.json
    """
    raw = os.getenv("NEWS_WEIGHTS_PATH", "") or ""
    if raw.strip():
        return Path(os.path.expandvars(os.path.expanduser(raw.strip())))
    return Path(r"C:\EA_AI\runtime\news_weights.json")


_news_weights_cache: Dict[str, object] = {
    "mtime": 0.0,
    "weights": {},  # type: ignore
}


def _load_news_weights_from_file(path: Path) -> Dict[str, float]:
    """
    يقرأ ملف news_weights.json ويعيد dict: {bucket -> weight(float)}.
    إذا حدث أي خطأ يعيد {}.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        data = json.loads(text)
    except Exception:
        return {}

    weights_raw = data.get("weights", {}) or {}
    out: Dict[str, float] = {}
    if isinstance(weights_raw, dict):
        for k, v in weights_raw.items():
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
    return out


def _get_news_weights_cached() -> Dict[str, float]:
    """
    كاش بسيط يعتمد على mtime للملف حتى لا نعيد القراءة في كل دورة.
    """
    path = _news_weights_path()
    if not path.exists():
        _news_weights_cache["mtime"] = 0.0
        _news_weights_cache["weights"] = {}
        return {}

    try:
        mtime = path.stat().st_mtime
    except Exception:
        return {}

    if mtime != _news_weights_cache.get("mtime", 0.0):
        w = _load_news_weights_from_file(path)
        _news_weights_cache["mtime"] = mtime
        _news_weights_cache["weights"] = w
        return w
    return _news_weights_cache.get("weights", {})  # type: ignore


def _resolve_decisions_paths() -> list[Path]:
    raw = os.getenv("DECISIONS_CSV_PATH", "") or ""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    out: list[Path] = []
    for p in parts:
        out.append(Path(os.path.expandvars(os.path.expanduser(p))))
    return out


def _latest_news_bucket_from_decisions(default: str = "none") -> str:
    """
    يحاول قراءة آخر قيمة news_level من آخر ملف decisions.csv
    (حسب DECISIONS_CSV_PATH). إذا لم يجد يعيد default.
    """
    paths = _resolve_decisions_paths()
    for p in paths:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            continue

        if len(lines) < 2:
            continue

        header = lines[0].strip()
        if not header:
            continue

        # تخمين الفاصل: إذا كان ';' أكثر من ',' نستخدمه، وإلا ','
        sep = ";" if header.count(";") >= header.count(",") else ","
        cols = [c.strip().lower() for c in header.split(sep) if c.strip()]
        if not cols:
            continue

        try:
            idx_news = cols.index("news_level")
        except ValueError:
            # لا يوجد عمود news_level في هذا الملف
            continue

        # البحث من الأسفل للأعلى عن آخر سطر فيه قيمة
        for ln in reversed(lines[1:]):
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split(sep)
            if idx_news >= len(parts):
                continue
            val = parts[idx_news].strip()
            if val != "":
                return val

    return default


def _map_bucket_to_weight(bucket: str, weights: Dict[str, float]) -> float:
    """
    يحاول إيجاد وزن مناسب للـ bucket من dict الأوزان.

    التسلسل:
      - الوزن حسب المفتاح نفسه (string)
      - نفس المفتاح lower/upper
      - bucket 'none' إن وجد
      - bucket '0' إن وجد
      - وإلا 1.0 (محايد)
    """
    if not weights:
        return 1.0

    key = str(bucket)
    if key in weights:
        return float(weights[key])

    key_l = key.lower()
    key_u = key.upper()
    if key_l in weights:
        return float(weights[key_l])
    if key_u in weights:
        return float(weights[key_u])

    if "none" in weights:
        return float(weights["none"])
    if "0" in weights:
        return float(weights["0"])

    return 1.0


def resolve_news_bucket_and_weight() -> Tuple[str, float]:
    """
    نقطة دخول موحّدة:
      - يقرأ آخر news_level من decisions
      - يقرأ news_weights.json (كاش)
      - يعيد (bucket, weight)
    """
    bucket = _latest_news_bucket_from_decisions(default="none")
    weights = _get_news_weights_cached()
    w = _map_bucket_to_weight(bucket, weights)
    return bucket, float(w)


# =========================
# Live Config (SelfCal) reader (cached)
# =========================
_live_cache: Dict[str, object] = {"mtime": 0.0, "data": {}}


def _live_config_paths() -> list[Path]:
    """
    يحاول إيجاد live_config.json من أكثر من مكان (شركة كبرى = robust paths):
      1) LIVE_CONFIG_PATH من env
      2) C:\\EA_AI\\runtime\\live_config.json
      3) MT5 Common Files\\live_config.json
      4) MT5 Common Files\\ea_ai\\live_config.json
    """
    paths: list[Path] = []

    raw = (os.getenv("LIVE_CONFIG_PATH", "") or "").strip()
    if raw:
        paths.append(Path(os.path.expandvars(os.path.expanduser(raw))))

    paths.append(Path(r"C:\EA_AI\runtime\live_config.json"))

    try:
        common = Path(_mt5_common_files())
        paths.append(common / "live_config.json")
        paths.append(common / "ea_ai" / "live_config.json")
    except Exception:
        pass

    out: list[Path] = []
    for p in paths:
        try:
            if p.exists() and p.is_file():
                out.append(p)
        except Exception:
            continue
    return out


def _read_live_config_cached() -> dict:
    """
    كاش بسيط: يقرأ أحدث live_config.json حسب mtime.
    """
    best: Path | None = None
    best_m = -1.0

    for p in _live_config_paths():
        try:
            m = p.stat().st_mtime
            if m > best_m:
                best = p
                best_m = m
        except Exception:
            continue

    if best is None:
        _live_cache["mtime"] = 0.0
        _live_cache["data"] = {}
        return {}

    if float(best_m) == float(_live_cache.get("mtime", 0.0)):
        return _live_cache.get("data", {}) or {}

    try:
        txt = best.read_text(encoding="utf-8", errors="ignore")
        data = json.loads(txt) if txt.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    _live_cache["mtime"] = float(best_m)
    _live_cache["data"] = data
    return data


def _get_live_float(key: str, default: float) -> float:
    d = _read_live_config_cached()
    v = d.get(key, default)
    try:
        return float(v)
    except Exception:
        return float(default)


# =========================
# Public APIs
# =========================
def write_ini_signal(
    symbol: str,
    direction: str,
    confidence: float,
    rationale: str,
    hold_minutes: int = 30,
    rr: float = 2.0,
    risk_pct: float = 1.0,
    file_name: str | None = None,
) -> str:
    """
    يكتب INI بترميز UTF-16 LE + BOM مع CRLF. ويُنشئ نسخة مرآة في
    Common\\Files\\ai_signals لضمان أن الإكسبرت يقرأها.
    """
    sym = (symbol or "").upper().strip()
    if not file_name:
        file_name = f"{sym.lower()}_signal.ini"

    ts_epoch = str(int(time.time()))
    lines = [
        f"ts={ts_epoch}",
        f"symbol={sym}",
        f"direction={(direction or '').upper()}",
        f"confidence={max(0.0, min(float(confidence), 0.999999)):.6f}",
        f"rationale={rationale or ''}",
        f"hold_minutes={int(hold_minutes)}",
        f"rr={float(rr):.6f}",
        f"risk_pct={float(risk_pct):.6f}",
        ""
    ]
    text = "\n".join(lines)

    # 1) المسار الرئيسي
    ai_dir = resolve_ai_dir()
    main_path = os.path.join(ai_dir, file_name)
    _write_utf16le_bom_crlf_atomic(main_path, text)

    # 2) المرآة إلى Common\Files\ai_signals
    mirror_dir = _ensure_dir(os.path.join(_mt5_common_files(), "ai_signals"))
    mirror_path = os.path.join(mirror_dir, file_name)
    if os.path.abspath(mirror_path) != os.path.abspath(main_path):
        _write_utf16le_bom_crlf_atomic(mirror_path, text)

    try:
        size = os.path.getsize(mirror_path)
        print(
            f"[writer] wrote ini -> path={mirror_path} "
            f"bytes={size} dir={(direction or '').upper()} conf={float(confidence):.3f}"
        )
    except Exception:
        pass

    return main_path


# =========================
# Helpers: read last INI
# =========================
def _latest_ini_dict(p: str) -> dict:
    try:
        txt = Path(p).read_text(encoding="utf-16", errors="ignore")
    except Exception:
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {}
    out = {}
    for ln in txt.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


# =========================
# Single-instance lock
# =========================
class SingleInstance:
    """
    قفل ملف بسيط يمنع تعدد النسخ. المسار ثابت: C:\\EA_AI\\artifacts\\locks\\writer.lock
    هذا يمنع اختلاف المسارات عند فشل قراءة .env.
    """
    def __init__(self):
        root = os.path.join("C:\\EA_AI", "artifacts", "locks")
        Path(root).mkdir(parents=True, exist_ok=True)
        self.lock_path = Path(root) / "writer.lock"
        self.fd: int | None = None

    def acquire(self) -> bool:
        try:
            self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self.fd, f"{os.getpid()}|{sys.executable}\n".encode("utf-8"))
            return True
        except FileExistsError:
            # تحقق من صلاحية الـ PID الموجود في الملف
            try:
                txt = self.lock_path.read_text(encoding="utf-8", errors="ignore").strip()
                pid = int((txt.split("|")[0]).strip()) if txt else -1
            except Exception:
                pid = -1

            if pid > 0 and not _pid_exists(pid):
                # الـ process القديم ميت → احذف lock وأعد المحاولة
                try:
                    self.lock_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return self.acquire()
            return False

    def release(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.lock_path.exists():
                self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform.startswith("win"):
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, 0, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


# ===========================================================
# Entrypoint: auto signal writer loop
# ===========================================================
if __name__ == "__main__":
    # لا نستعمل python-dotenv إطلاقًا هنا.
    try:
        if settings is None:
            from app.config import settings as _settings
        else:
            from importlib import reload
            _settings = reload(sys.modules["app.config"]).settings  # type: ignore
    except Exception as e:
        print("[writer][FATAL] config import error:", e)
        sys.exit(2)

    # طبّق AI_SIGNALS_DIR من settings إن وُجد
    _AI_DIR = getattr(_settings, "AI_SIGNALS_DIR", _AI_DIR) or _AI_DIR

    # منع النسخ المتعددة بقفل بمسار ثابت
    lock = SingleInstance()
    if not lock.acquire():
        print("[writer] another instance is already running. exiting.")
        sys.exit(0)

    try:
        def _as_bool(x, default=True):
            try:
                if isinstance(x, bool):
                    return x
                s = str(x).strip().lower()
                return s in ("1", "true", "yes", "y", "on")
            except Exception:
                return default

        def _as_int(x, default):
            try:
                return int(x)
            except Exception:
                try:
                    return int(float(x))
                except Exception:
                    return default

        def _as_float(x, default):
            try:
                return float(x)
            except Exception:
                return default

        sym   = (getattr(_settings, "SYMBOL", "XAUUSD") or "XAUUSD").upper()
        ena   = _as_bool(getattr(_settings, "AUTO_WRITE_ENABLED", True), True)
        secs  = _as_int(getattr(_settings, "AUTO_WRITE_INTERVAL_SEC", 60), 60)
        force = _as_bool(getattr(_settings, "AUTO_WRITE_FORCE", True), True)

        # defaults فقط — سيتم override من live_config أثناء التشغيل
        holdm_default = _as_int(getattr(_settings, "HOLD_MINUTES_DEFAULT", 30), 30)
        rr_default    = _as_float(getattr(_settings, "RR_DEFAULT", 2.0), 2.0)
        risk_default  = _as_float(getattr(_settings, "RISK_PCT_DEFAULT", 1.0), 1.0)
        base_min_conf_default = _as_float(getattr(_settings, "AI_MIN_CONFIDENCE_DEFAULT", 0.65), 0.65)

        if not ena:
            print("[writer] AUTO_WRITE_ENABLED=0 -> exit")
            sys.exit(0)

        secs = max(30, secs)

        fname = f"{sym.lower()}_signal.ini"
        mirror_dir = os.path.join(
            os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files", "ai_signals"
        )
        os.makedirs(mirror_dir, exist_ok=True)
        mirror_path = os.path.join(mirror_dir, fname)

        from app.services.aggregator import generate_direction_confidence

        print(f"[writer] auto-signal loop start  pid={os.getpid()} exe={sys.executable}")
        print(
            f"[writer] sym={sym} every={secs}s force={force} "
            f"ai_dir={resolve_ai_dir()} mirror={mirror_path}"
        )

        while True:
            try:
                # 1) توليد إشارة خام (بدون ضرب بالـ news_weight)
                dir_raw, conf_raw, why = generate_direction_confidence(sym, force=force)
                conf_raw = float(max(0.0, min(float(conf_raw), 0.999999)))

                # 2) اقرأ live_config (SelfCal) بشكل cached
                holdm = int(_get_live_float("hold_minutes", float(holdm_default)))
                rr    = float(_get_live_float("rr", float(rr_default)))
                risk  = float(_get_live_float("risk_pct", float(risk_default)))
                base_min_conf = float(_get_live_float("ai_min_confidence", float(base_min_conf_default)))

                # clamp آمن
                holdm = max(1, min(720, int(holdm)))
                rr    = max(0.5, min(10.0, float(rr)))
                risk  = max(0.01, min(5.0, float(risk)))
                base_min_conf = max(0.30, min(0.99, float(base_min_conf)))

                # 3) الأخبار: نستخدمها لتعديل العتبة (threshold) وليس لضرب الثقة
                news_bucket, news_weight = resolve_news_bucket_and_weight()

                # news_weight تفسيره هنا: >1 تشديد, <1 تخفيف (robust)
                # نحول وزن الأخبار إلى تعديل للعتبة:
                # - إذا news_weight = 0.80 => نخّفض العتبة قليلًا
                # - إذا news_weight = 1.20 => نرفع العتبة
                min_conf_required = base_min_conf * float(max(0.70, min(1.30, news_weight)))
                min_conf_required = float(max(0.30, min(0.99, min_conf_required)))

                # 4) Gate "شركة كبرى": إذا لا يحقق العتبة → FLAT
                allowed = 1
                dir_final = (dir_raw or "").upper()
                conf_final = conf_raw

                if dir_final not in ("BUY", "SELL") or conf_raw < min_conf_required:
                    allowed = 0
                    dir_final = "FLAT"
                    conf_final = 0.0

                # rationale موسّع للتشخيص
                why_ext = (why or "").strip()
                extra = (
                    f"news_bucket={news_bucket}, news_w={news_weight:.3f}, "
                    f"conf_raw={conf_raw:.3f}, base_min_conf={base_min_conf:.3f}, "
                    f"min_req={min_conf_required:.3f}, allowed={allowed}, "
                    f"risk={risk:.3f}, rr={rr:.3f}, holdm={holdm}"
                )
                rationale = f"{why_ext} | {extra}" if why_ext else extra

                # 5) منع الكتابة المتكررة إلا عند تغيّر مهم
                prev = _latest_ini_dict(mirror_path)
                prev_dir = (prev.get("direction", "") or "").upper()
                try:
                    prev_conf = float(prev.get("confidence", "0") or 0.0)
                except Exception:
                    prev_conf = 0.0

                changed = (dir_final != prev_dir) or (abs(conf_final - prev_conf) >= 0.02)

                if changed:
                    path = write_ini_signal(
                        symbol=sym,
                        direction=dir_final,
                        confidence=conf_final,
                        rationale=rationale,
                        hold_minutes=holdm,
                        rr=rr,
                        risk_pct=risk,
                        file_name=fname,
                    )
                    print(
                        "[writer] updated "
                        f"dir={dir_final} conf_raw={conf_raw:.3f} conf_final={conf_final:.3f} "
                        f"min_req={min_conf_required:.3f} allowed={allowed} "
                        f"news_bucket={news_bucket} w={news_weight:.3f} file={path}"
                    )
                else:
                    print(
                        "[writer] no-change "
                        f"dir={dir_final} conf_raw={conf_raw:.3f} conf_final={conf_final:.3f} "
                        f"min_req={min_conf_required:.3f} allowed={allowed} "
                        f"news_bucket={news_bucket} w={news_weight:.3f}"
                    )

            except Exception as e:
                print("[writer][ERR]", type(e).__name__, str(e))

            jitter = random.uniform(-0.2, 0.2) * secs
            time.sleep(max(15, int(secs + jitter)))
    finally:
        lock.release()
