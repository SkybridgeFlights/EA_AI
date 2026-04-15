from __future__ import annotations
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

def main():
    if not mt5.initialize():
        print("MT5 initialize() FAILED:", mt5.last_error())
        return

    term = mt5.terminal_info()
    acc  = mt5.account_info()
    print("Terminal:", term)
    print("Account :", acc)

    # ابحث عن رموز الذهب الشائعة
    all_syms = mt5.symbols_get()
    names = [s.name for s in all_syms] if all_syms else []
    cand = [n for n in names if "XAU" in n.upper()]
    print("\nXAU candidates (first 50):")
    for n in cand[:50]:
        print(" ", n)

    # جرّب تلقائيًا أول 5 مرشحين على H1 (آخر 30 يوم)
    tf = mt5.TIMEFRAME_H1
    dt_end = datetime.now(timezone.utc)
    dt_start = dt_end - timedelta(days=30)

    print("\nTesting rates for candidates:")
    for n in cand[:10]:
        r = mt5.copy_rates_range(n, tf, dt_start, dt_end)
        cnt = 0 if r is None else len(r)
        print(f"  {n:15s} -> bars: {cnt}")

    mt5.shutdown()

if __name__ == "__main__":
    main()