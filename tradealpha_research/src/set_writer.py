# src/set_writer.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any

def _mt5_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    # MT5 يقبل الأرقام كنص
    return str(v)

def write_set(params: Dict[str, Any], set_path: str) -> None:
    """
    Writes a simple MT5 .set style file:
      Key=Value
    (هذا النوع مناسب لاستخدامه عبر ExpertParameters=... في ini)
    """
    p = Path(set_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    for k, v in params.items():
        if v is None:
            continue
        lines.append(f"{k}={_mt5_value(v)}")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")