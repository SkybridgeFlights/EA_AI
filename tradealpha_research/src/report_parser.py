import re
from pathlib import Path

def _find_float(pattern: str, text: str):
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).replace("\xa0", "").replace(" ", "").replace(",", "")
    try:
        return float(val)
    except:
        return None

def parse_mt5_report(html_path: str) -> dict:
    p = Path(html_path)
    if not p.exists():
        raise FileNotFoundError(f"Report not found: {p}")

    txt = p.read_text(errors="ignore")

    net_profit = _find_float(r"Total Net Profit<\/td>\s*<td[^>]*>\s*([-\d\.,]+)", txt)
    pf = _find_float(r"Profit Factor<\/td>\s*<td[^>]*>\s*([-\d\.,]+)", txt)

    # Drawdown relative often appears as percentage already
    dd = _find_float(r"Drawdown Relative<\/td>\s*<td[^>]*>\s*([-\d\.,]+)", txt)
    trades = _find_float(r"Total Trades<\/td>\s*<td[^>]*>\s*([-\d\.,]+)", txt)

    return {
        "net_profit": net_profit,
        "profit_factor": pf,
        "max_dd_pct": dd,
        "trades": trades,
        "report": str(p),
    }