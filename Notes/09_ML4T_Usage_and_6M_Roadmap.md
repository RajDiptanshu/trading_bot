# 09 — What ML4T logic is actually in the code, and the 6-month roadmap

*2026-06-11. ML4T = Stefan Jansen, "Machine Learning for Trading" (the repo in
C:\trading_bot\machine-learning-for-trading). This doc answers "what logic was
used from ML4T" exactly, file by file, and lays out the path to the robust
program treating ML4T as the reference text.*

## Part 1 — ML4T concepts already implemented

### ml_signal.py + train_ml.py (the ML layer — built, NOT YET TRAINED)
| ML4T source | What the code does |
|---|---|
| ch4 (alpha factors) | 19 features: lagged returns (1–126d), **12-1 momentum**, RSI(14)/RSI(2), Bollinger %B, MACD z-score, NATR, ATR z-score, OBV slope, volume ratio, distance from 52w high, ADX, WorldQuant **alpha#101**, dollar-volume z |
| ch4/ch6 (data prep) | **Cross-sectional rank transform per date** — features become percentile ranks across the watchlist, robust to outliers, comparable across stocks |
| ch11/12 (boosted trees) | LightGBM → sklearn HistGradientBoosting → LogisticRegression fallback; binary label = "beats the watchlist median over the next 5 days" |
| ML4T utils.py (MultipleTimeSeriesCV) | **Purged walk-forward CV**: train strictly before test, 5-day gap purged so the forward-return label can't leak |
| ch4 (signal evaluation) | Per-fold AUC + daily **rank-IC** (Spearman of predicted prob vs realized forward return) — the information-coefficient standard |

### agents.py (Agents 1–3)
- **12-1 momentum** (ch4 standard momentum, skips last month to avoid short-term
  reversal) — in `rs_ranks()` and as a Kestner confirmation.
- **Momentum-laggard veto** (own backtest decile study, method from ch4): RS rank < 40 = no trade.
- **Quality factors** (ch4): ROE, D/E, margin, revenue growth → 0–4 score, tiebreaker only.
- **LM-style sentiment word lists** (ch14, Loughran-McDonald approach): finance-specific
  positive/negative word counts on headlines, plus kill-words.
- **ML floor in the playbook**: prob < 0.40 forces WAIT; > 0.60 supports conviction.
  The model can only veto/tilt — it never originates trades. This is deliberate (ch8:
  ML signals need a risk framework around them, not the other way round).

### agent4.py (executor)
- Kelly-capped sizing (Kestner ch11, consistent with ML4T ch5 risk framing).
- **Every decision logged with the full feature snapshot** (`agent4_decisions.jsonl`) —
  this builds the labelled dataset ML4T ch4–6 needs, automatically, every day.

### What is NOT yet done from ML4T
1. **The model has never been trained** — `models/ml_signal.pkl` does not exist until
   `python train_ml.py` runs (now scheduled Sundays 18:00).
2. No per-stock IV surface / options ML (ch20-ish territory) — options use chain LTPs only.
3. No backtest of the ML-filtered strategy vs unfiltered (ch8 discipline) — planned below.

## Part 2 — 6-month roadmap (ML4T as the bible)

**Month 1 (now):** paper-trade daily via the scheduled Agent 4 tasks. First ML training
run Sunday; record CV AUC and rank-IC in this folder. Honesty gate: if mean rank-IC < 0.02,
the signal is noise — keep it as a veto-only layer, do not size with it.

**Month 2:** Agent 5 daily report + ablation backtest (ch8): same V10 system with and
without the ML floor, identical costs. Keep the filter only if it improves PF/maxDD
out-of-sample. Wire per-stock option-chain history capture (we now fetch chains daily —
log them; that becomes the IV dataset nothing free provides retroactively).

**Month 3:** evaluate paper book (need ≥ 30 closed trades): PF > 1.3, expectancy > 0 after
costs, maxDD < 10%. Retrain monthly with walk-forward (never on the paper trades alone —
40-80 samples overfit instantly; paper results VALIDATE, history TRAINS).

**Month 4:** if gates pass — feature importance pruning (ch12 SHAP), add earnings/event
features (ch14), try 10-day horizon label alongside 5-day. If gates fail — fix the base
strategy first; ML cannot rescue a negative-expectancy system (ch1's core warning).

**Month 5:** options sizing upgrade: use logged IV history for percentile-based premium
richness per stock (Sinclair + ch15 vol concepts) instead of India-VIX-only.

**Month 6:** go/no-go review for real capital. Hard gates: 3+ months paper, ≥ 50 trades,
PF > 1.3 across two regimes, every kill switch observed firing correctly at least once.

## Part 3 — honest note on "100% accuracy / full authority"
No system reaches 100% accuracy; the target is positive expectancy with controlled
drawdown. The scheduled tasks give the algo full autonomy on PAPER: it views the market,
enters, manages, and exits without you. Going live (real Angel One orders) must be a
deliberate human decision after the Month-6 gates — and should start at a fraction
(10–20%) of intended size. Nothing in the current code places real orders.
