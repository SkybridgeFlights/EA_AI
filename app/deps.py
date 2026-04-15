#deps,py
import os
from pathlib import Path
from app.config import settings

def mt5_ai_signal_path(symbol: str) -> Path:
    base = Path(settings.MT5_FILES_DIR) / "ai_signals"
    base.mkdir(parents=True, exist_ok=True)
    name = f"{symbol.lower()}_signal.ini"
    return base / name

def mt5_news_csv_path(symbol: str) -> Path:
    base = Path(settings.MT5_FILES_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"news_{symbol.lower()}.csv"





