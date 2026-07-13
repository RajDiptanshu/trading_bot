"""
train_ml.py — fetch watchlist history and train the optional ML signal.

Usage (from the recommender folder, venv active):
    python train_ml.py              # ~7 years of dailies, purged walk-forward CV, saves models/ml_signal.pkl
    python train_ml.py --period 5y  # shorter history

Requires: scikit-learn (and optionally lightgbm), scipy, joblib.
    pip install scikit-learn scipy joblib lightgbm

Read the CV table before trusting the model:
  - mean rank-IC > 0.02 is usable signal for a 5-day horizon (ML4T benchmark);
  - mean AUC ~0.52-0.55 is normal for daily equity data - anything >0.60 on
    this little data usually means leakage, treat with suspicion;
  - retrain monthly (weekly_tuner can call this) - edges decay (Kestner half-life).
"""
import argparse, sys, json, time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents
import ml_signal


def fetch_history(period: str) -> dict:
    """[V10.1 2026-07-06] Train on the TRADING UNIVERSE (default nifty210, ~200 names),
    not just the 55-name watchlist: cross-sectional ML needs breadth — the 55-name train
    was noise (IC -0.011), the 204-name train passes the gate (IC +0.037 @ 10d label).
    One bulk threaded download instead of per-symbol loops."""
    import os
    try:
        import universe as uni
        syms = [e["symbol"] for e in uni.load_universe(os.getenv("AGENT4_UNIVERSE_SCOPE", "nifty210"))]
    except Exception:
        syms = [s["symbol"] for s in agents.load_watchlist()]
    print(f"downloading {len(syms)} symbols ({period} daily, one bulk call)...")
    raw = yf.download([s + ".NS" for s in syms], period=period, interval="1d",
                      auto_adjust=True, group_by="ticker", progress=False, threads=True, timeout=40)
    prices = {}
    for sym in syms:
        try:
            df = (raw[sym + ".NS"] if len(syms) > 1 else raw).dropna(subset=["Close"])
            if len(df) >= ml_signal.MIN_HIST:
                prices[sym] = df
        except Exception:
            continue
    print(f"got {len(prices)} usable symbols")
    return prices


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="7y", help="yfinance period, e.g. 5y, 7y, max")
    ap.add_argument("--ensemble", action="store_true",
                    help="[V11.1] train the 10-seed ENSEMBLE (models/ml_ensemble.pkl) that "
                         "agent4's AGENT4_ML_GATE consumes, instead of the single model")
    args = ap.parse_args()
    prices = fetch_history(args.period)
    if len(prices) < 15:
        print("FATAL: fewer than 15 symbols with enough history - aborting")
        sys.exit(1)
    if args.ensemble:
        print("training the 10-seed ENSEMBLE (purged walk-forward CV + fits)...")
        meta = ml_signal.train_ensemble(prices)
        print(json.dumps({k: v for k, v in meta.items() if k != "cv"}, indent=1))
        return
    print("training (purged walk-forward CV)...")
    meta = ml_signal.train(prices)
    print(json.dumps({k: v for k, v in meta.items() if k != "cv"}, indent=1))
    ic = meta.get("mean_ic")
    if ic is None:
        print("WARNING: zero CV folds completed (history too short for the default")
        print("         504d-train/63d-test walk-forward). The saved model has NO")
        print("         out-of-sample evidence - use --period 7y or longer, or do")
        print("         not rely on ml fields until a CV-validated model exists.")
    elif ic < 0.01:
        print("WARNING: mean rank-IC < 0.01 - model has little/no signal on this universe.")
        print("         The recommender will still run; ML fields just add noise. Consider")
        print("         deleting models/ml_signal.pkl until more data/features are available.")


if __name__ == "__main__":
    main()
