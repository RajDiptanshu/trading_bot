"""
ml_lab.py — ML experiments on the expanded universe (2026-07-06).

Question: the 55-name train found NO signal (mean rank-IC -0.011 -> noise-gated off).
Does 4x cross-sectional breadth (204 names) + label variants change that?

Configs (same 19 features, same purged walk-forward CV, same LightGBM):
  A  5d  beat-median label   (the live config, on 4x breadth)
  B  10d beat-median label   (slower horizon, closer to the 5-20d hold)
  C  5d  vol-scaled label    (fwd_ret / 20d vol beats median — rewards risk-adjusted moves)

HONESTY RULES: experiments write to scratch (never models/ml_signal.pkl — the live model
stays gated); the verdict is mean rank-IC vs the 0.01 noise gate, reported as-is.
"""
import pickle, sys, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

BOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR / "Algo Trading" / "recommender"))
import ml_signal as ms

SCRATCH = BOT_DIR / "backtest_results"


def load_prices():
    with open(SCRATCH / "prices_uni210.pkl", "rb") as f:
        data = pickle.load(f)
    raw = data["raw"]
    prices = {}
    for t in raw.columns.get_level_values(0).unique():
        if not str(t).endswith(".NS"):
            continue
        df = raw[t].dropna(subset=["Close"])
        if len(df) >= ms.MIN_HIST:
            prices[str(t)[:-3]] = df
    return prices


def build_panel_vol_scaled(prices, fwd_days=5):
    """Variant C: label = vol-scaled forward return beats cross-sectional median."""
    frames = []
    for sym, df in prices.items():
        if len(df) < ms.MIN_HIST:
            continue
        f = ms.symbol_features(df)
        vol = df.Close.pct_change().rolling(20).std()
        f["fwd_ret"] = (df.Close.pct_change(fwd_days).shift(-fwd_days)) / (vol + 1e-9)
        f["symbol"] = sym
        frames.append(f)
    panel = pd.concat(frames).set_index("symbol", append=True)
    panel.index.names = ["date", "symbol"]
    ranked = panel.groupby(level="date")[ms.FEATURES].rank(pct=True)
    ranked["fwd_ret"] = panel["fwd_ret"]
    med = panel.groupby(level="date")["fwd_ret"].transform("median")
    ranked["label"] = (panel["fwd_ret"] > med).astype(int)
    return ranked.dropna(subset=ms.FEATURES + ["fwd_ret"])


def cv_eval(panel, tag):
    """Purged walk-forward CV -> per-fold AUC + rank-IC (ml_signal's exact procedure)."""
    from sklearn.metrics import roc_auc_score
    from scipy.stats import spearmanr
    X, y = panel[ms.FEATURES], panel["label"]
    folds = []
    for k, (tr, te) in enumerate(ms.PurgedWalkForwardCV().split(panel)):
        if len(tr) < 1000 or len(te) < 200:
            continue
        model, name = ms._make_model()
        model.fit(X.iloc[tr], y.iloc[tr])
        prob = model.predict_proba(X.iloc[te])[:, 1]
        auc = roc_auc_score(y.iloc[te], prob)
        sub = panel.iloc[te].copy(); sub["prob"] = prob
        daily = sub.groupby(level="date").apply(
            lambda g: spearmanr(g["prob"], g["fwd_ret"])[0] if len(g) > 5 else np.nan)
        ic = float(np.nanmean(daily))
        folds.append((round(float(auc), 4), round(ic, 4), len(tr), len(te)))
        print(f"  {tag} fold{k}: AUC {auc:.4f}  rank-IC {ic:+.4f}  (train {len(tr):,} test {len(te):,})")
    if folds:
        m_auc = round(float(np.mean([f[0] for f in folds])), 4)
        m_ic = round(float(np.mean([f[1] for f in folds])), 4)
        gate = "PASSES" if m_ic >= ms.MIN_USABLE_IC else "FAILS"
        print(f"  {tag} MEAN: AUC {m_auc}  rank-IC {m_ic:+.4f}  -> {gate} the 0.01 noise gate\n")
        return m_auc, m_ic
    return None, None


def main():
    prices = load_prices()
    print(f"universe panel: {len(prices)} symbols\n")
    results = {}

    print("A) 5d beat-median (live config, 4x breadth)")
    ms.FWD_DAYS = 5
    pA = ms.build_panel(prices, with_labels=True)
    print(f"   obs: {len(pA):,}")
    results["A_5d_median"] = cv_eval(pA, "A")

    print("B) 10d beat-median")
    ms.FWD_DAYS = 10
    pB = ms.build_panel(prices, with_labels=True)
    print(f"   obs: {len(pB):,}")
    results["B_10d_median"] = cv_eval(pB, "B")
    ms.FWD_DAYS = 5

    print("C) 5d vol-scaled beat-median")
    pC = build_panel_vol_scaled(prices, 5)
    print(f"   obs: {len(pC):,}")
    results["C_5d_volscaled"] = cv_eval(pC, "C")

    print("=== VERDICT ===")
    for k, (auc, ic) in results.items():
        print(f"  {k:16} AUC {auc}  IC {ic}  {'PASS' if (ic or -1) >= ms.MIN_USABLE_IC else 'fail'}")


if __name__ == "__main__":
    main()
