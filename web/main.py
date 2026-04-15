# web/main.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import os

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.now().isoformat()}

# مجلد ملفات الإشارة (عدّل حسب مسار طرفك لو لزم)
AI_DIR = os.getenv(
    "AI_SIGNALS_DIR",
    r"C:\Users\Wajd Shaaban\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\ai_signals"
)
os.makedirs(AI_DIR, exist_ok=True)

class SignalIn(BaseModel):
    symbol: str = Field(..., examples=["XAUUSD"])
    direction: str = Field(..., examples=["BUY", "SELL"])
    confidence: float = Field(..., ge=0, le=1, examples=[0.82])
    rationale: str = Field("", examples=["EMA cross + regime trend"])
    hold_minutes: int = Field(30, ge=0, le=240)
    rr: float = Field(2.0, gt=0)
    risk_pct: float = Field(0.5, gt=0, le=5.0)
    file_name: str = Field("xauusd_signal.ini", description="اسم الملف داخل مجلد ai_signals")

def build_ini_text(s: SignalIn) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y.%m.%d %H:%M:%S")
    lines = [
        f"ts={now}",
        f"symbol={s.symbol}",
        f"direction={s.direction.upper()}",
        f"confidence={s.confidence:.4f}",
        f"rationale={s.rationale}",
        f"hold_minutes={s.hold_minutes}",
        f"rr={s.rr}",
        f"risk_pct={s.risk_pct}",
        ""
    ]
    # CRLF مثل نوتباد
    return "\r\n".join(lines)

def write_ini_file(path: str, text: str, encoding: str):
    # encoding: "utf16" => UTF-16 LE with BOM, "utf8" => UTF-8 (بدون BOM)
    if encoding == "utf16":
        with open(path, "w", encoding="utf-16", newline="") as f:
            f.write(text)
    elif encoding == "utf8":
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    else:
        raise ValueError("encoding must be 'utf16' or 'utf8'")

@app.post("/signal/write")
def write_signal(
    sig: SignalIn,
    encoding: str = Query("utf16", pattern="^(utf16|utf8)$", description="utf16 أو utf8")
):
    d = sig.direction.upper()
    if d not in ("BUY", "SELL"):
        raise HTTPException(400, "direction must be BUY or SELL")
    text = build_ini_text(sig)
    path = os.path.join(AI_DIR, sig.file_name)
    try:
        write_ini_file(path, text, encoding)
    except Exception as e:
        raise HTTPException(500, f"write failed: {e}")
    return {"ok": True, "path": path, "encoding": encoding}

@app.post("/signal/clear")
def clear_signal(file_name: str = "xauusd_signal.ini"):
    path = os.path.join(AI_DIR, file_name)
    try:
        if os.path.exists(path):
            os.remove(path)
        return {"ok": True, "removed": not os.path.exists(path)}
    except Exception as e:
        raise HTTPException(500, f"clear failed: {e}")