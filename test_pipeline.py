# test_pipeline.py
import os, argparse
from app.services.fetch import generate_direction_confidence
from app.services.writer import write_ini_signal, resolve_ai_dir

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--force", action="store_true", help="استخدم فول-باك تقني عند غياب ML")
    p.add_argument("--hold", type=int, default=30)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--risk", type=float, default=0.5)
    p.add_argument("--file", default="xauusd_signal.ini")
    p.add_argument("--use-gc", action="store_true", help="تحويل XAUUSD إلى GC=F")
    args = p.parse_args()

    if args.use_gc:
        os.environ["USE_GC_F_FOR_XAU"] = "true"

    d, c, r = generate_direction_confidence(args.symbol, force=args.force)
    print(f"direction={d}  confidence={c:.4f}")
    print(f"reason={r}")

    path = write_ini_signal(args.symbol, d, c, r,
                            hold_minutes=args.hold,
                            rr=args.rr,
                            risk_pct=args.risk,
                            file_name=args.file)
    print("written:", path)
    print("AI dir:", resolve_ai_dir())

if __name__ == "__main__":
    main()