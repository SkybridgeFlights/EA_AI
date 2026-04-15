# -*- coding: utf-8 -*-writer_guard.py
import os, time, json, traceback
from pathlib import Path

def atomic_write_text(path: Path, text: str, encoding: str="utf-8"):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding=encoding)
    os.replace(str(tmp), str(path))

def write_ini_signal_safe(dst_path: str, symbol: str, direction: str, confidence: float,
                          rationale: str = "", max_retries: int = 5, backoff_sec: float = 0.5) -> dict:
    """
    يكتب ini إلى المسار المشترك Common\\Files\\ai_signals بذريّة + إعادة المحاولة.
    """
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # صيغة INI المتوقعة من الإكسبرت
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    ini = (
        f"[signal]\n"
        f"symbol={symbol}\n"
        f"direction={direction}\n"
        f"confidence={confidence:.3f}\n"
        f"ts={ts}\n"
        f"note={rationale}\n"
    )

    for i in range(1, max_retries + 1):
        try:
            atomic_write_text(dst, ini, encoding="utf-8")
            return {
                "ok": True,
                "symbol": symbol,
                "direction": direction,
                "confidence": round(confidence, 3),
                "written_file": str(dst),
                "ts": ts,
                "retries": i - 1,
            }
        except Exception as e:
            if i >= max_retries:
                return {"ok": False, "error": str(e), "trace": traceback.format_exc()[:2000]}
            time.sleep(backoff_sec * i)  # backoff تزايدي








