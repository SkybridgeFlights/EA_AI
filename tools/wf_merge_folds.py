# tools/wf_merge_folds.py
import argparse
import re
from pathlib import Path

import pandas as pd


FOLD_RE = re.compile(r"fold[_-]?(\d+)", re.IGNORECASE)


def _read_signal_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    # expected columns: dt, dir, conf, p_buy, p_sell, p_flat, margin
    if "dt" not in df.columns or "dir" not in df.columns:
        raise ValueError(f"Bad signals file (missing dt/dir): {p}")
    df["dt"] = pd.to_datetime(df["dt"], utc=True, errors="coerce")
    df = df.dropna(subset=["dt"]).copy()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="WF run root folder (contains fold_XXX subfolders)")
    ap.add_argument("--out", required=True, help="Output merged CSV path")
    ap.add_argument("--symbol", default="XAUUSDr")
    ap.add_argument("--tf", default="M15")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    files = list(root.rglob(f"signals_{args.symbol}_{args.tf}_fold*.csv"))
    if not files:
        # fallback: any signals_... inside fold folders
        files = list(root.rglob("signals_*_fold*.csv"))

    if not files:
        raise SystemExit(f"No fold signals found under: {root}")

    rows = []
    manifest = []

    def fold_id_from_path(p: Path) -> int:
        m = FOLD_RE.search(str(p))
        if m:
            return int(m.group(1))
        # fallback: from filename fold001
        m2 = re.search(r"fold(\d+)", p.name, re.IGNORECASE)
        return int(m2.group(1)) if m2 else 0

    files = sorted(files, key=fold_id_from_path)

    for f in files:
        fid = fold_id_from_path(f)
        df = _read_signal_csv(f)
        df["fold"] = fid

        t0 = df["dt"].min()
        t1 = df["dt"].max()
        manifest.append({"fold": fid, "file": str(f), "from": str(t0), "to": str(t1), "rows": int(len(df))})

        rows.append(df)

    merged = pd.concat(rows, axis=0, ignore_index=True)

    # keep last occurrence per dt (important if overlap)
    merged = merged.sort_values(["dt", "fold"]).drop_duplicates("dt", keep="last")

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(outp, index=False)

    # write manifest next to out
    manp = outp.with_suffix(".manifest.json")
    pd.DataFrame(manifest).to_json(manp, orient="records", indent=2)

    print("OK")
    print("fold files:", len(files))
    print("merged rows:", len(merged))
    print("out:", outp)
    print("manifest:", manp)


if __name__ == "__main__":
    main()
