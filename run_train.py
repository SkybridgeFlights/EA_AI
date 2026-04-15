import os
import pandas as pd

os.chdir(r"C:\EA_AI")

csv_in  = r"C:\EA_AI\data\XAUUSD_H1.csv"
csv_out = r"C:\EA_AI\data\XAUUSD_H1_utf8.csv"

print("Converting encoding...")

# جرب قراءة بفاصل فاصلة
try:
    df = pd.read_csv(csv_in, encoding="utf-16", sep=",")
    if df.shape[1] < 4:
        df = pd.read_csv(csv_in, encoding="utf-16", sep="\t")
except:
    df = pd.read_csv(csv_in, encoding="utf-16", sep="\t")

print("Columns found:", list(df.columns))
print("Shape:", df.shape)

# توحيد أسماء الأعمدة
df.columns = [c.strip().lower() for c in df.columns]
rename = {
    "time": "time",
    "<date>": "time", "date": "time",
    "<open>": "open", "<high>": "high",
    "<low>": "low",   "<close>": "close",
    "<tickvol>": "tick_volume", "<vol>": "tick_volume",
}
df = df.rename(columns=rename)
print("After rename:", list(df.columns))

df.to_csv(csv_out, index=False, encoding="utf-8")
print(f"Converted! rows={len(df)} -> {csv_out}")

os.environ["TRAIN_PRICES_CSV"] = csv_out

from app.ml.model import train_and_save
print("Starting training...")
path, report = train_and_save()
print("Model saved:", path)
print("Accuracy:", report.get("accuracy", "N/A"))