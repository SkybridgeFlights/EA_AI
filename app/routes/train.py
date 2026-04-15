from fastapi import APIRouter, HTTPException
import traceback
from app.ml.model import train_and_save, switch_active_model
from app.ml.registry import list_models, get_active_model_path

router = APIRouter(prefix="/train")

@router.post("/start")
def start_train():
    try:
        out_path, report = train_and_save()
        return {"saved_model": out_path, "report": report}
    except Exception:
        tb = traceback.format_exc()
        raise HTTPException(status_code=400, detail=f"Training failed:\n{tb}")

@router.get("/active")
def active_model():
    return {"active_model_path": get_active_model_path()}

@router.post("/activate")
def activate(path: str):
    p = switch_active_model(path)
    return {"active_model_path": p}

@router.get("/list")
def list_all():
    return {"models": list_models()}







