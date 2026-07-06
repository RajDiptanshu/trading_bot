"""
ml_signal.py — optional ML layer for the recommender (V10).

Approach = machine-learning-for-trading (Stefan Jansen) ch.4/6/11/12/24:
  features : returns at multiple lags, 12-1 momentum, RSI(14)/RSI(2), %B,
             MACD (z-scored), NATR, ATR z-score, OBV slope, volume ratio,
             distance from 52w high, ADX, alpha#101 (close-open)/(high-low),
             dollar-volume z — each cross-sectionally RANKED per date
             (rank transform makes features comparable across stocks and
             robust to outliers; standard ML4T practice).
  label    : forward FWD_DAYS-day return ABOVE the cross-sectional median (binary).
  model    : LightGBM -> sklearn HistGradientBoosting -> LogisticRegression
             (first available wins; all gradient-boosted-tree-or-simpler).
  CV       : purged walk-forward (MultipleTimeSeriesCV pattern from ML4T
             utils.py): test windows move back in time, train strictly
             before test, 5-day lookahead gap purged to stop label leakage.
  metrics  : per-fold AUC + daily rank-IC (Spearman of P(up) vs realized
             forward return) — the ML4T 'information coefficient' standard.

Train:    python train_ml.py            (writes models/ml_signal.pkl)
Inference: score_universe() -> {symbol: {prob, asof, model, trained}}
The recommender works WITHOUT this; everything degrades gracefully.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR  = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
MODEL_FILE = MODEL_DIR / "ml_signal.pkl"

FWD_DAYS = 10         # label horizon (and purge gap)
                      # [V10.1 2026-07-06] 5->10d: ml_lab on the 204-name universe —
                      # 10d beat-median label: mean rank-IC +0.037 (5d: +0.024), all
                      # positive folds, strongest in the most recent window (+0.072).
                      # The 55-name train was IC -0.011 (noise): breadth was the fix.
MIN_HIST = 300        # bars needed per symbol

FEATURES = ["ret_1", "ret_5", "ret_10", "ret_21", "ret_63", "ret_126",
            "mom_12_1", "rsi14", "rsi2", "bb_pctb", "macd_z", "natr",
            "atr_z", "obv_slope", "vol_ratio", "dist_52w_high", "adx",
            "alpha101", "dollar_vol_z"]

# ───────────────────────── indicator helpers (no TA-Lib dependency) ─────────────────────────
def _rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([df.High - df.Low,
                    (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df.High.diff(), -df.Low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([df.High - df.Low,
                    (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    atrn = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atrn
    mdi = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atrn
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

def _zscore(s: pd.Series, n: int = 63) -> pd.Series:
    return (s - s.rolling(n).mean()) / s.rolling(n).std()

def symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol raw features, index = date. df needs OHLCV >= MIN_HIST bars."""
    c, v = df.Close, df.Volume
    f = pd.DataFrame(index=df.index)
    for t in (1, 5, 10, 21, 63, 126):
        f[f"ret_{t}"] = c.pct_change(t)
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    f["rsi14"] = _rsi(c, 14)
    f["rsi2"]  = _rsi(c, 2)
    mid = c.rolling(20).mean(); sd = c.rolling(20).std()
    f["bb_pctb"] = (c - (mid - 2 * sd)) / (4 * sd)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd_z"] = _zscore(macd, 126)
    atr = _atr(df)
    f["natr"]  = atr / c * 100
    f["atr_z"] = _zscore(atr, 63)
    obv = (v * np.sign(c.diff()).fillna(0)).cumsum()
    f["obv_slope"] = (obv - obv.shift(10)) / (obv.rolling(63).std() + 1e-9)
    f["vol_ratio"] = v / v.rolling(20).mean()
    f["dist_52w_high"] = c / df.High.rolling(252, min_periods=120).max() - 1
    f["adx"] = _adx(df)
    f["alpha101"] = (c - df.Open) / (df.High - df.Low + 1e-3)     # WorldQuant alpha#101
    f["dollar_vol_z"] = _zscore(np.log((c * v).rolling(20).mean()), 126)
    return f

def build_panel(prices: dict[str, pd.DataFrame], with_labels: bool = True) -> pd.DataFrame:
    """prices: {symbol: OHLCV df}. Returns MultiIndex (date, symbol) frame with
    cross-sectionally ranked features in [0,1] (+ label if with_labels)."""
    frames = []
    for sym, df in prices.items():
        if len(df) < (MIN_HIST if with_labels else 270):
            continue
        f = symbol_features(df)
        if with_labels:
            f["fwd_ret"] = df.Close.pct_change(FWD_DAYS).shift(-FWD_DAYS)
        f["symbol"] = sym
        frames.append(f)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames).set_index("symbol", append=True)
    panel.index.names = ["date", "symbol"]
    # cross-sectional rank transform per date (ML4T standard)
    ranked = panel.groupby(level="date")[FEATURES].rank(pct=True)
    if with_labels:
        ranked["fwd_ret"] = panel["fwd_ret"]
        med = panel.groupby(level="date")["fwd_ret"].transform("median")
        ranked["label"] = (panel["fwd_ret"] > med).astype(int)
        ranked = ranked.dropna(subset=FEATURES + ["fwd_ret"])
    else:
        ranked = ranked.dropna(subset=FEATURES)
    return ranked

# ───────────────────────── purged walk-forward CV (ML4T utils.py pattern) ─────────────────────────
class PurgedWalkForwardCV:
    def __init__(self, n_splits=4, train_days=504, test_days=63, lookahead=FWD_DAYS):
        self.n_splits, self.train_days, self.test_days, self.lookahead = n_splits, train_days, test_days, lookahead

    def split(self, panel: pd.DataFrame):
        days = sorted(panel.index.get_level_values("date").unique(), reverse=True)
        dates = panel.reset_index()[["date"]]
        for i in range(self.n_splits):
            te_end, te_start = i * self.test_days, i * self.test_days + self.test_days
            tr_end = te_start + self.lookahead            # purge gap
            tr_start = tr_end + self.train_days
            if tr_start >= len(days):
                break
            te_idx = dates[(dates.date > days[te_start]) & (dates.date <= days[te_end])].index
            tr_idx = dates[(dates.date > days[tr_start]) & (dates.date <= days[tr_end])].index
            yield tr_idx.to_numpy(), te_idx.to_numpy()

def _make_model():
    try:
        from lightgbm import LGBMClassifier
        return LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                              min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                              random_state=42, verbosity=-1), "lightgbm"
    except Exception:
        pass
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                              min_samples_leaf=50, random_state=42), "sklearn-hgb"
    except Exception:
        pass
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=1000), "logistic"

def train(prices: dict[str, pd.DataFrame], verbose: bool = True) -> dict:
    """Cross-validate, then fit on all data and persist. Returns metrics dict."""
    from sklearn.metrics import roc_auc_score
    from scipy.stats import spearmanr
    import joblib
    panel = build_panel(prices, with_labels=True)
    if panel.empty:
        raise RuntimeError("not enough data to build a training panel")
    X, y = panel[FEATURES], panel["label"]
    folds = []
    for k, (tr, te) in enumerate(PurgedWalkForwardCV().split(panel)):
        if len(tr) < 1000 or len(te) < 200:
            continue
        model, name = _make_model()
        model.fit(X.iloc[tr], y.iloc[tr])
        prob = model.predict_proba(X.iloc[te])[:, 1]
        auc = roc_auc_score(y.iloc[te], prob)
        sub = panel.iloc[te].copy(); sub["prob"] = prob
        daily_ic = sub.groupby(level="date").apply(
            lambda g: spearmanr(g["prob"], g["fwd_ret"])[0] if len(g) > 5 else np.nan)
        ic = float(np.nanmean(daily_ic))
        folds.append({"fold": k, "auc": round(float(auc), 4), "rank_ic": round(ic, 4),
                      "train_n": len(tr), "test_n": len(te)})
        if verbose:
            print(f"  fold {k}: AUC {auc:.4f}  rank-IC {ic:+.4f}  (train {len(tr)}, test {len(te)})")
    model, name = _make_model()
    model.fit(X, y)
    MODEL_DIR.mkdir(exist_ok=True)
    meta = {"model_name": name, "features": FEATURES, "fwd_days": FWD_DAYS,
            "trained": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n_obs": len(panel), "cv": folds,
            "mean_auc": round(float(np.mean([f["auc"] for f in folds])), 4) if folds else None,
            "mean_ic": round(float(np.mean([f["rank_ic"] for f in folds])), 4) if folds else None}
    joblib.dump({"model": model, "meta": meta}, MODEL_FILE)
    if verbose:
        print(f"  saved {MODEL_FILE.name}: {name}, mean AUC {meta['mean_auc']}, mean IC {meta['mean_ic']}")
    return meta

# ───────────────────────── inference ─────────────────────────
MIN_USABLE_IC = 0.01   # NOISE GATE: below this mean rank-IC the model is noise.
                       # Inference returns {} so the whole system runs as "no ML"
                       # (ml=None everywhere) instead of taking random vetoes.
                       # Weekly retrains keep running; if IC ever clears this bar,
                       # the model switches on automatically. (2026-06-11 train:
                       # mean IC -0.0059 -> gated off, by design.)

def score_universe() -> dict:
    """Latest P(beat median next FWD_DAYS) for every symbol in the TRADING UNIVERSE
    (env AGENT4_UNIVERSE_SCOPE, default nifty210 — the cross-section the model was
    trained on; falls back to the watchlist if universe.py unavailable).
    Empty dict if no model OR if the saved model failed the noise gate.
    Uses agents.history() (bulk-prefetched) so data is shared/cached."""
    if not MODEL_FILE.exists():
        return {}
    import joblib
    blob = joblib.load(MODEL_FILE)
    model, meta = blob["model"], blob["meta"]
    ic = meta.get("mean_ic")
    if ic is None or ic < MIN_USABLE_IC:
        return {}                                  # noise gate — see above
    import agents                                            # late import, no cycle at module load
    try:
        import universe as uni
        syms = [e["symbol"] for e in uni.load_universe(os.getenv("AGENT4_UNIVERSE_SCOPE", "nifty210"))]
    except Exception:
        syms = [s["symbol"] for s in agents.load_watchlist()]
    try:
        agents.prefetch_history(syms, "27mo")                # one bulk fetch, warms the cache
    except Exception:
        pass
    prices = {}
    for sym in syms:
        try:
            df = agents.history(sym, "27mo")
            if len(df) >= 270:
                prices[sym] = df
        except Exception:
            continue
    panel = build_panel(prices, with_labels=False)
    if panel.empty:
        return {}
    last_date = panel.index.get_level_values("date").max()
    latest = panel.xs(last_date, level="date")
    probs = model.predict_proba(latest[meta["features"]])[:, 1]
    asof = str(pd.Timestamp(last_date).date())
    return {sym: {"prob": round(float(p), 3), "asof": asof,
                  "model": meta["model_name"], "trained": meta["trained"],
                  "cv_mean_auc": meta.get("mean_auc"), "cv_mean_ic": meta.get("mean_ic")}
            for sym, p in zip(latest.index, probs)}
