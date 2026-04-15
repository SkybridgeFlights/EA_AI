# src/wfo.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional
import pandas as pd


@dataclass(frozen=True)
class WFOWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def __repr__(self) -> str:
        return (
            f"WFOWindow(train={self.train_start.date()}→{self.train_end.date()}, "
            f"test={self.test_start.date()}→{self.test_end.date()})"
        )


def _to_utc_ts(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def build_wfo_windows(
    df: pd.DataFrame,
    train_months: int = 18,
    test_months: int = 6,
    step_months: int = 6,
    min_test_bars: int = 200,
    time_col: str = "time",
) -> List[WFOWindow]:
    """
    Rolling time windows:
      Train: [t, t+train_months)
      Test : [t+train_months, t+train_months+test_months)
      Step : shift t by step_months
    """
    if time_col not in df.columns:
        raise ValueError(f"df missing '{time_col}' column")

    t0 = _to_utc_ts(df[time_col].min())
    t1 = _to_utc_ts(df[time_col].max())

    windows: List[WFOWindow] = []
    cur = t0

    # Ensure we start from the first bar time (floor to day)
    cur = cur.floor("D")

    while True:
        train_start = cur
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        if test_end > t1:
            break

        # Check enough bars in test segment
        mask_test = (df[time_col] >= test_start) & (df[time_col] < test_end)
        if int(mask_test.sum()) >= int(min_test_bars):
            windows.append(
                WFOWindow(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

        cur = cur + pd.DateOffset(months=step_months)

    return windows


def slice_window(
    df: pd.DataFrame, w: WFOWindow, time_col: str = "time"
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    train = df[(df[time_col] >= w.train_start) & (df[time_col] < w.train_end)].copy()
    test = df[(df[time_col] >= w.test_start) & (df[time_col] < w.test_end)].copy()
    return train, test