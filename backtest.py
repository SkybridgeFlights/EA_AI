import pandas as pd
import numpy as np
from pathlib import Path

CSV_PATH = r"data\XAUUSD_H1.csv"


def load_data(p):
    df = pd.read_csv(p)

    # توحيد أسماء الأعمدة إلى lowercase ثم إعادة تسميتها للاسم القياسي
    cols = {c.lower(): c for c in df.columns}
    df.columns = [c.lower() for c in df.columns]

    # دعم كلا الحالتين: timestamp أو time
    if "timestamp" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"timestamp": "time"})

    rename_map = {
        "time": "Time",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        # لو موجود volume نخليه
        "volume": "Volume",
    }
    df = df.rename(columns=rename_map)

    needed = {"Time", "Open", "High", "Low", "Close"}
    if not needed.issubset(df.columns):
        raise ValueError(
            f"الملف لا يحتوي الأعمدة المطلوبة: {needed}. "
            f"الأعمدة الموجودة: {list(df.columns)}"
        )

    df["Time"] = pd.to_datetime(df["Time"], errors="coerce", utc=True)
    df = df.dropna(subset=["Time"]).set_index("Time").sort_index()
    return df


def simple_backtest(df):
    """
    باكتيست بسيط:
    - شراء إذا أغلقت الشمعة فوق SMA 50
    - بيع إذا أغلقت تحت SMA 50
    """
    df["SMA50"] = df["Close"].rolling(50, min_periods=1).mean()
    df["Position"] = 0
    df.loc[df["Close"] > df["SMA50"], "Position"] = 1   # BUY
    df.loc[df["Close"] < df["SMA50"], "Position"] = -1  # SELL

    df["Returns"] = df["Close"].pct_change().fillna(0.0)
    df["Strategy"] = df["Position"].shift(1).fillna(0.0) * df["Returns"]

    cum_ret = (1 + df["Returns"]).cumprod() - 1
    cum_str = (1 + df["Strategy"]).cumprod() - 1
    return cum_ret, cum_str


if __name__ == "__main__":
    df = load_data(CSV_PATH)
    cum_ret, cum_str = simple_backtest(df)

    print("📊 Backtest Results")
    print(f"Buy & Hold Return: {cum_ret.iloc[-1]:.2%}")
    print(f"Strategy Return: {cum_str.iloc[-1]:.2%}")



    