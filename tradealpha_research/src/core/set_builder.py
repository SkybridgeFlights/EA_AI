import os
from typing import Dict, Any


def _to_mt5_value(v: Any) -> str:
    """
    Convert python values to MT5 .set compatible values.
    - bool -> "true"/"false"
    - enum name string like "TM_TECH_ONLY" -> try map (optional), else keep
    - numbers -> str
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


class SetFileBuilder:
    def __init__(self, base_config: Dict[str, Any]):
        self.base_config = dict(base_config)

    def _normalize(self, merged: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fix common MT5 quirks:
        - ENUM inputs should be numeric in .set (TradeMode: 0/1/2)
        """
        out = dict(merged)

        # --- Critical: TradeMode is ENUM -> must be numeric ---
        # Accept: 0/1/2 or names; force numeric for safety
        if "TradeMode" in out:
            tm = out["TradeMode"]
            if isinstance(tm, str):
                name = tm.strip()
                mapping = {
                    "TM_AI_ONLY": 0,
                    "TM_HYBRID": 1,
                    "TM_TECH_ONLY": 2,
                    "0": 0, "1": 1, "2": 2,
                }
                out["TradeMode"] = mapping.get(name, 2)  # default force TECH
            elif isinstance(tm, (int, float)):
                out["TradeMode"] = int(tm)
            else:
                out["TradeMode"] = 2

        # Force TECH ONLY + disable AI signals in the file (no manual edits)
        out["TradeMode"] = 2
        out["UseAISignals"] = False

        return out

    def save(self, dynamic: Dict[str, Any], output_path: str) -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        merged = dict(self.base_config)
        merged.update(dynamic or {})
        merged = self._normalize(merged)

        lines = []
        for k in sorted(merged.keys()):
            lines.append(f"{k}={_to_mt5_value(merged[k])}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return os.path.abspath(output_path)