# C:\EA_AI\app\config\settings.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os
from pathlib import Path
from typing import List, Optional, Any

from pydantic import BaseSettings, Field, validator
from dotenv import load_dotenv

# حمّل .env من جذر المشروع
ROOT = Path(__file__).resolve().parents[2]  # C:\EA_AI
load_dotenv(dotenv_path=ROOT / ".env", override=False)

def _strpath(v: Optional[str], default: str) -> str:
    return str(Path(v) if v else Path(default))

class Settings(BaseSettings):
    # MT5 dirs
    MT5_COMMON_FILES: str = Field(default_factory=lambda: _strpath(os.getenv("MT5_COMMON_FILES"), r"C:\Users\Public\Documents\MetaQuotes\Terminal\Common\Files"))
    MT5_FILES_DIR:    str = Field(default_factory=lambda: _strpath(os.getenv("MT5_FILES_DIR"), r"C:\Users\Public\Documents\MetaQuotes\Terminal\Common\Files"))

    # Core paths
    TRADE_LOGS_DIR:   str = Field(default_factory=lambda: _strpath(os.getenv("TRADE_LOGS_DIR"), r"C:\EA_AI\logs"))
    AI_SIGNALS_DIR:   str = Field(default_factory=lambda: _strpath(os.getenv("AI_SIGNALS_DIR"), r"C:\EA_AI\ai_signals"))
    MODEL_DIR:        str = Field(default_factory=lambda: _strpath(os.getenv("MODEL_DIR"), r"C:\EA_AI\models"))
    LIVE_CONFIG_PATH: str = Field(default_factory=lambda: _strpath(os.getenv("LIVE_CONFIG_PATH"), r"C:\EA_AI\live_config.json"))
    MIRROR_LIVE_CONFIG_TO: str = os.getenv("MIRROR_LIVE_CONFIG_TO", "")

    DEALS_CSV_PATH:   str = Field(default_factory=lambda: _strpath(os.getenv("DEALS_CSV_PATH"), r"C:\EA_AI\logs\deals.csv"))
    JSONL_DIR:        str = Field(default_factory=lambda: _strpath(os.getenv("JSONL_DIR"), r"C:\EA_AI\artifacts\jsonl"))
    JSONL_FILE_PREFIX:str = os.getenv("JSONL_FILE_PREFIX", "trades_")
    ARTIFACTS_DIR:    str = Field(default_factory=lambda: _strpath(os.getenv("ARTIFACTS_DIR"), r"C:\EA_AI\artifacts"))

    # Self-Cal flags
    AUTO_SELFCAL_ENABLED: bool = os.getenv("AUTO_SELFCAL_ENABLED", "true").lower() in ("1","true","yes","on")
    AUTO_SELFCAL_INTERVAL_SEC: int = int(os.getenv("AUTO_SELFCAL_INTERVAL_SEC", "900"))
    SELFCAL_SHADOW: bool = os.getenv("SELFCAL_SHADOW", "true").lower() in ("1","true","yes","on")

    # CORS
    CORS_ALLOW_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])

    # Calendar guards
    CAL_NO_TRADE_BEFORE: int = int(os.getenv("CAL_NO_TRADE_BEFORE", "5"))
    CAL_NO_TRADE_AFTER:  int = int(os.getenv("CAL_NO_TRADE_AFTER", "5"))
    CAL_MIN_IMPACT:      int = int(os.getenv("CAL_MIN_IMPACT", "2"))

    # Scope defaults
    SCOPE_SYMBOL: str = os.getenv("SCOPE_SYMBOL", "XAUUSD")
    SCOPE_TF:     str = os.getenv("SCOPE_TF", "M15")
    SYMBOL:       str = os.getenv("SYMBOL", "XAUUSD")

    @validator("CORS_ALLOW_ORIGINS", pre=True)
    def parse_origins(cls, v: Any) -> List[str]:
        if isinstance(v, list):
            return v
        s = str(v).strip()
        if s.startswith("["):
            try:
                return list(json.loads(s))
            except Exception:
                pass
        if s:
            return [x.strip() for x in s.split(",") if x.strip()]
        return ["*"]

settings = Settings()











