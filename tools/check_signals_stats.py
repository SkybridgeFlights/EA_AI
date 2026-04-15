import os
import pandas as pd

_mt5_files = os.getenv("MT5_FILES_DIR", "")
p = os.path.join(_mt5_files, "ai_signals", "signals_XAUUSDr_M15_2020-2024.csv") if _mt5_files else ""
if not p or not os.path.exists(p):
    raise SystemExit(
        f"File not found: {p!r}\n"
        "Set MT5_FILES_DIR in .env to your MT5 MQL5/Files folder."
    )
df = pd.read_csv(p)

print("rows:", len(df))
print("columns:", list(df.columns))

# اكتشاف أسماء الأعمدة المحتملة
conf_col = "conf" if "conf" in df.columns else ("confidence" if "confidence" in df.columns else None)
margin_col = "margin" if "margin" in df.columns else None

# p_flat قد يكون اسمه مختلف أو غير موجود
pflat_col = None
for cand in ["p_flat", "prob_flat", "flat", "p0", "proba_flat"]:
    if cand in df.columns:
        pflat_col = cand
        break

if conf_col is None:
    raise SystemExit("Missing confidence column. Expected one of: conf/confidence")

print(f"Using columns -> conf={conf_col}, margin={margin_col}, p_flat={pflat_col}")

# لو p_flat غير موجود: سنحسب eligible بناءً على conf فقط أو conf+margin إن وجدت
def eligible_mask(th: float):
    m = (df[conf_col] >= th)
    if margin_col is not None:
        m = m & (df[margin_col] >= 0.06)
    if pflat_col is not None:
        m = m & (df[pflat_col] <= 0.55)
    return m

for th in [0.60, 0.62, 0.63, 0.64, 0.65, 0.66, 0.70]:
    ok = eligible_mask(th)
    print(th, "eligible:", int(ok.sum()))

print("\nconf describe:\n", df[conf_col].describe(percentiles=[.5, .75, .9, .95, .99]))
if margin_col is not None:
    print("\nmargin describe:\n", df[margin_col].describe(percentiles=[.5, .75, .9, .95, .99]))
if pflat_col is not None:
    print("\np_flat describe:\n", df[pflat_col].describe(percentiles=[.5, .75, .9, .95, .99]))
