# app/ml/replay_registry.py
import json
from pathlib import Path
import joblib
from xgboost import XGBClassifier
from app.config import settings

def _replay_active_file() -> Path:
    # ملف مستقل عن active_model.json
    # سيكون: C:/EA_AI/models/active_replay_model.json
    return Path(settings.MODEL_DIR) / "active_replay_model.json"

def get_active_replay_model_path() -> str | None:
    f = _replay_active_file()
    if not f.exists():
        return None
    try:
        x = json.loads(f.read_text(encoding="utf-8"))
        return x.get("active")
    except Exception:
        return None

def set_active_replay_model(path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    reg = {"active": str(p).replace("\\", "/")}
    _replay_active_file().write_text(json.dumps(reg, indent=2), encoding="utf-8")

def load_active_replay_model() -> XGBClassifier | None:
    ap = get_active_replay_model_path()
    if not ap:
        return None
    p = Path(ap)
    if not p.exists():
        return None
    try:
        return joblib.load(p)
    except Exception:
        return None
