# metrics.py - Analytics Metrics (Fixed)
# [FIX] what_if signature mismatch with main.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import os
import pandas as pd


@dataclass
class RollingWindowMetrics:
    window_days: int
    trades: int
    pf: float
    wr: float
    max_dd_pct: float
    pnl: float


def _load_deals_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Deals CSV not found at {path}")

    df = pd.read_csv(path)

    time_cols = [
        c for c in df.columns
        if c.lower() in ("time", "ts", "timestamp", "open_time", "close_time")
    ]
    if not time_cols:
        raise ValueError(f"No time-like column found. Columns: {list(df.columns)}")
    tcol = time_cols[0]

    df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol])

    type_col  = next((c for c in df.columns if c.lower() in ("type","deal_type")), None)
    entry_col = next((c for c in df.columns if c.lower() in ("entry","deal_entry")), None)
    if type_col and entry_col:
        df = df[(df[entry_col] == "OUT") | (df[entry_col] == "OUT_BY")]

    pnl_cols = [c for c in df.columns if c.lower() in ("profit","pnl","pnl_usd","p&l")]
    if not pnl_cols:
        raise ValueError(f"No profit column. Columns: {list(df.columns)}")
    pcol = pnl_cols[0]

    df = df[[tcol, pcol]].rename(columns={tcol: "time", pcol: "pnl"})
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _max_drawdown_pct(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    dd = (equity_curve - running_max) / running_max.replace(0, float("nan"))
    dd = dd.fillna(0.0)
    return float(dd.min() * 100.0)


def compute_rolling_metrics(
    deals_csv_path: Path,
    windows: Iterable[int] = (7, 30, 90),
) -> Dict[int, RollingWindowMetrics]:
    deals_csv_path = Path(deals_csv_path)
    df = _load_deals_csv(deals_csv_path)
    if df.empty:
        return {}

    now = datetime.now(timezone.utc)
    out: Dict[int, RollingWindowMetrics] = {}

    for w in windows:
        cutoff = now - timedelta(days=w)
        sub = df[df["time"] >= cutoff].copy()
        if sub.empty:
            out[w] = RollingWindowMetrics(window_days=w, trades=0, pf=0.0, wr=0.0, max_dd_pct=0.0, pnl=0.0)
            continue

        pnl      = sub["pnl"]
        pnl_pos  = pnl[pnl > 0]
        pnl_neg  = pnl[pnl < 0]

        gross_win  = float(pnl_pos.sum())
        gross_loss = float(pnl_neg.sum())

        pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else (float("inf") if gross_win > 0 else 0.0)
        wr = float(pnl_pos.count() / max(1, len(sub)) * 100.0)
        eq = pnl.cumsum()

        out[w] = RollingWindowMetrics(
            window_days=w,
            trades=len(sub),
            pf=pf,
            wr=wr,
            max_dd_pct=_max_drawdown_pct(eq),
            pnl=float(pnl.sum()),
        )

    return out


def rolling_metrics(
    days: int = 30,
    deals_csv_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if deals_csv_path is None:
        deals_csv_path = Path(os.getenv("DEALS_CSV_PATH", ""))

    try:
        all_metrics = compute_rolling_metrics(deals_csv_path, windows=(days,))
    except Exception as e:
        return {"window_days": days, "trades": 0, "pf": 0.0, "wr": 0.0,
                "max_dd_pct": 0.0, "pnl": 0.0, "error": str(e)}

    m = all_metrics.get(days)
    return asdict(m) if m else {"window_days": days, "trades": 0, "pf": 0.0,
                                "wr": 0.0, "max_dd_pct": 0.0, "pnl": 0.0}


def what_if(
    scenario: Optional[Dict[str, Any]] = None,
    *,
    rr: Optional[float] = None,
    risk_pct: Optional[float] = None,
    ts_start: Optional[int] = None,
    ts_step: Optional[int] = None,
    be_trig: Optional[int] = None,
    be_offs: Optional[int] = None,
) -> Dict[str, Any]:
    sc: Dict[str, Any] = dict(scenario or {})
    if rr is not None:       sc["rr"]        = rr
    if risk_pct is not None: sc["risk_pct"]  = risk_pct
    if ts_start is not None: sc["ts_start"]  = ts_start
    if ts_step is not None:  sc["ts_step"]   = ts_step
    if be_trig is not None:  sc["be_trig"]   = be_trig
    if be_offs is not None:  sc["be_offs"]   = be_offs

    deals_csv_path = Path(os.getenv("DEALS_CSV_PATH", ""))
    base_metrics_30d: Dict[str, Any] = {}

    if deals_csv_path.exists():
        try:
            m30 = compute_rolling_metrics(deals_csv_path, windows=(30,)).get(30)
            if m30:
                base_metrics_30d = asdict(m30)
        except Exception as e:
            base_metrics_30d = {"error": str(e)}

    return {
        "scenario": sc,
        "base_metrics_30d": base_metrics_30d,
        "note": "what_if is a placeholder; no live changes are applied.",
    }