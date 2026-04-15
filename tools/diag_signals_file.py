import argparse
import pandas as pd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--min_conf", type=float, default=0.65)
    ap.add_argument("--max_flat", type=float, default=0.55)
    ap.add_argument("--min_margin", type=float, default=0.06)
    ap.add_argument("--use_flat", action="store_true")
    ap.add_argument("--use_margin", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    print("rows:", len(df))
    print("cols:", list(df.columns))

    for c in ["conf", "dir"]:
        if c not in df.columns:
            raise RuntimeError(f"missing column {c}")

    # base eligibility
    ok = (df["conf"] >= args.min_conf)

    if args.use_flat:
        if "p_flat" not in df.columns:
            raise RuntimeError("p_flat missing; regenerate with latest generate_ai_replay_csv_xgb.py")
        ok = ok & (df["p_flat"] <= args.max_flat)

    if args.use_margin:
        if "margin" not in df.columns:
            raise RuntimeError("margin missing; regenerate with latest generate_ai_replay_csv_xgb.py")
        ok = ok & (df["margin"] >= args.min_margin)

    dsel = df.loc[ok, "dir"]
    print("eligible:", int(ok.sum()))
    print("eligible dir counts:\n", dsel.value_counts(dropna=False))
    print("all dir counts:\n", df["dir"].value_counts(dropna=False))
    print("\nconf describe:\n", df["conf"].describe())

    if "p_buy" in df.columns and "p_sell" in df.columns and "p_flat" in df.columns:
        print("\nmean probs:\n", df[["p_buy","p_sell","p_flat"]].mean())
        print("\nmax prob winner counts:")
        winner = df[["p_buy","p_sell","p_flat"]].idxmax(axis=1)
        print(winner.value_counts())

if __name__ == "__main__":
    main()
