# src/setfile.py
from __future__ import annotations

from typing import Dict, Any
import codecs


def _parse_value(v: str) -> Any:
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"

    # int?
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    except Exception:
        pass

    # float?
    try:
        return float(s)
    except Exception:
        return s


def read_set_file(path: str) -> Dict[str, Any]:
    """
    Reads MT5 .set (often UTF-16LE with BOM) and returns dict of CURRENT values.
    Example line:
      InpMAfast=12||20||2||200||N
    First value before '||' is the current value.
    """
    with codecs.open(path, "r", encoding="utf-16") as f:
        lines = f.read().splitlines()

    out: Dict[str, Any] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith(";"):
            continue
        if "=" not in ln:
            continue

        k, rest = ln.split("=", 1)
        k = k.strip()
        val = rest.split("||", 1)[0]
        out[k] = _parse_value(val)

    return out