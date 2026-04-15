# app/data/news_store.py
from pathlib import Path
import pandas as pd, requests
from app.config import settings

DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
NEWS_PARQUET = DATA_DIR / "te_calendar.parquet"

def fetch_te_range(start:str, end:str)->pd.DataFrame:
    # start/end: "YYYY-MM-DD"
    url = "https://api.tradingeconomics.com/calendar"
    params = {"d1": start, "d2": end}
    if settings.TE_API_KEY: params["c"] = settings.TE_API_KEY
    r = requests.get(url, params=params, timeout=20); r.raise_for_status()
    rows=[]
    for it in (r.json() or []):
        t = it.get("Date") or it.get("DateSpan") or ""
        try: ts = pd.to_datetime(t, utc=True)
        except: continue
        imp = str(it.get("Importance") or "").lower()
        impn = 3 if "high" in imp else (2 if "medium" in imp else 1)
        rows.append({"time":ts,"impact":impn,"currency":(it.get("Currency") or "").upper()})
    return pd.DataFrame(rows)

def build_store(start:str, end:str)->str:
    df = fetch_te_range(start, end).sort_values("time")
    df.to_parquet(NEWS_PARQUET, index=False)
    return str(NEWS_PARQUET)

def load_store()->pd.DataFrame:
    if NEWS_PARQUET.exists(): return pd.read_parquet(NEWS_PARQUET)
    return pd.DataFrame(columns=["time","impact","currency"])
