import re
from pathlib import Path
import csv

def _num(s: str):
    s = s.replace("\xa0", " ").strip()
    s = s.replace(" ", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def parse_report(html_path: Path) -> dict:
    text = html_path.read_text(encoding="utf-8", errors="ignore")

    def grab(label: str):
        # يحاول التقاط قيمة بعد label في HTML (مرن)
        patterns = [
            rf"{re.escape(label)}\s*</td>\s*<td[^>]*>\s*([^<]+)\s*<",
            rf"{re.escape(label)}\s*[:]\s*([^<\r\n]+)",
        ]
        for p in patterns:
            m = re.search(p, text, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    out = {"file": str(html_path)}

    # أشهر الحقول
    fields = {
        "Total Net Profit": "net_profit",
        "Profit Factor": "profit_factor",
        "Expected Payoff": "expected_payoff",
        "Recovery Factor": "recovery_factor",
        "Sharpe Ratio": "sharpe",
        "Balance Drawdown Maximal": "bal_dd_max",
        "Equity Drawdown Maximal": "eq_dd_max",
        "Total Trades": "trades",
        "Profit Trades (% of total)": "winrate_line",
    }

    for k, key in fields.items():
        v = grab(k)
        out[key] = v

    # تحويل أرقام
    for key in ["net_profit", "profit_factor", "expected_payoff", "recovery_factor", "sharpe"]:
        if out.get(key) is not None:
            out[key] = _num(out[key])

    # Trades (int)
    if out.get("trades") is not None:
        m = re.search(r"(\d+)", out["trades"])
        out["trades"] = int(m.group(1)) if m else None

    # winrate: "64 (53.33%)"
    if out.get("winrate_line"):
        m = re.search(r"\(([\d\.]+)\%\)", out["winrate_line"])
        out["winrate_pct"] = float(m.group(1)) if m else None
    else:
        out["winrate_pct"] = None

    return out

def main():
    root = Path(r"C:\EA_AI\reports\walk_forward_runs")
    reports = sorted(root.rglob("Report_fold*.html"))
    if not reports:
        print("No reports found under:", root)
        return

    rows = [parse_report(p) for p in reports]
    out_csv = root / "walkforward_summary.csv"

    keys = ["file", "net_profit", "profit_factor", "expected_payoff", "recovery_factor", "sharpe",
            "bal_dd_max", "eq_dd_max", "trades", "winrate_pct"]

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})

    print("WROTE:", out_csv)

if __name__ == "__main__":
    main()
