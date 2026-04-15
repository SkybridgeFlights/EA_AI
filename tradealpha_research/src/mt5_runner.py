# src/mt5_runner.py
from __future__ import annotations

import time
import subprocess
from pathlib import Path
from typing import Optional, Tuple

def run_mt5_terminal(
    mt5_terminal: str,
    ini_path: str,
    log_path: Optional[str] = None,
    timeout_sec: int = 60 * 60 * 6
) -> Tuple[int, float]:
    """
    Runs MT5 Strategy Tester in headless mode:
      terminal64.exe /config:<ini>
    Writes stdout/stderr into log_path (if provided).
    Returns (return_code, elapsed_seconds).
    """
    terminal = Path(mt5_terminal)
    ini = Path(ini_path)

    if not terminal.exists():
        raise FileNotFoundError(f"MT5 terminal not found: {terminal}")
    if not ini.exists():
        raise FileNotFoundError(f"INI not found: {ini}")

    cmd = [str(terminal), f"/config:{str(ini)}"]

    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        out, err = p.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        p.kill()
        raise RuntimeError(f"MT5 test timeout after {timeout_sec} sec. ini={ini}")

    elapsed = time.time() - t0

    if log_path:
        lp = Path(log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(
            "CMD:\n" + " ".join(cmd) + "\n\nSTDOUT:\n" + (out or "") + "\n\nSTDERR:\n" + (err or ""),
            encoding="utf-8",
            errors="ignore",
        )

    return p.returncode, elapsed