# app/ml/registry.py
import json
from pathlib import Path
import joblib
from app.config import settings

def list_models():
    p = Path(settings.MODEL_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return [str(x) for x in p.glob("*.bin")]

def get_active_model_path() -> str | None:
    f = Path(settings.ACTIVE_MODEL_FILE)
    if not f.exists():
        return None
    try:
        x = json.loads(f.read_text(encoding="utf-8"))
        return x.get("active")
    except:
        return None

def set_active_model(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = {"active": str(path).replace("\\", "/")}
    Path(settings.ACTIVE_MODEL_FILE).write_text(json.dumps(reg, indent=2), encoding="utf-8")

def load_active_model():
    """
    Backward-compatible:
    - returns sklearn-like model (has predict_proba) OR None
    """
    x = load_active_model_any()
    m = x.get("model")
    kind = x.get("kind")
    if kind == "sklearn":
        return m
    return None

def load_active_model_any() -> dict:
    """
    Robust loader:
    - kind: "sklearn" (joblib) OR "booster" (xgboost.Booster) OR "none"
    - model: loaded object or None
    - path: active path or None
    - error: last error string (optional)
    """
    ap = get_active_model_path()
    if not ap:
        return {"kind": "none", "model": None, "path": None, "error": "No active path in active_model.json"}

    p = Path(ap)
    if not p.exists():
        return {"kind": "none", "model": None, "path": str(p), "error": "Active model path does not exist"}

    # 1) Try joblib (sklearn XGBClassifier)
    try:
        m = joblib.load(p)
        if hasattr(m, "predict_proba"):
            return {"kind": "sklearn", "model": m, "path": str(p)}
        # إذا انحمّل شيء غريب بدون predict_proba اعتبره فشل
    except Exception as e:
        joblib_err = str(e)
    else:
        joblib_err = "Loaded object has no predict_proba"

    # 2) Try Booster
    try:
        from xgboost import Booster
        b = Booster()
        b.load_model(str(p))
        return {"kind": "booster", "model": b, "path": str(p)}
    except Exception as e:
        booster_err = str(e)

    return {
        "kind": "none",
        "model": None,
        "path": str(p),
        "error": f"joblib_failed={joblib_err} | booster_failed={booster_err}",
    }

def save_model_binary(model, path: Path):
    """
    Saves sklearn-like model with joblib.
    (Booster models should be saved via Booster.save_model elsewhere.)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
