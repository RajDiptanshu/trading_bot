# Agent Upgrade Spec — V10 (Agents 1/2/3)

**Document 7** | 2026-06-11 | Sources: ML4T repo (Stefan Jansen, 24 chapters reviewed end-to-end), book1.pdf (Cohen, *The Bible of Options Strategies*), Book2.pdf (Sinclair, *Option Trading*), Book3.pdf (Kestner, *Quantitative Trading Strategies*), own Backtest Report 05.
**Files changed:** `Algo Trading/recommender/agents.py` (rewritten), `ml_signal.py` + `train_ml.py` (new), `morning_scan.py` (3 patches), `morning_crew.py` (1 bug fix), `backtest_v10.py` (new validation harness).

## 0. The unbiased verdict on the current system

Report 05 said it plainly and the books agree: the V9 composite has *some* selection power but, as deployed, it is a low-octane sleeve whose edge has been decaying since 2024 and went negative 2025–26. The biggest fixable problems were not the score weights — they were (a) trading in hostile regimes (no F1 hard veto), (b) a mean-reversion sleeve that mathematically could never fire, (c) exits that cap winners at 3×ATR while letting losers gap through stops, (d) judging the system on win-rate/profit-factor instead of risk-adjusted, time-stable measures, and (e) no mechanism to detect/adapt when the edge decays. V10 attacks all five.

## 1. What each source contributed

**Kestner (Book3)** — judge strategies by Sharpe AND K-ratio (equity-curve slope / slope error / N), not net profit, win% or PF, which he demolishes one by one (ch4). Strategy edges have a half-life; plot net profit by year and project — V9's 2024→2026 fade is textbook (his trend-following call of 1999 used the same method). Stop trading a system when its equity curve breaks 2 standard errors below its regression line (the kill-switch rule). A 2×ATR stop-from-entry improved channel breakouts on stocks AND futures (validates our 2×ATR initial stop). Profit-taking at 3×ATR and pyramiding both UNDERPERFORMED plain trend-following exits (challenges our fixed 3×ATR target — hence the hybrid exit). ADX regime routing: counter-trend RSI systems work best ADX 20–30; trend systems at the extremes; for stocks, low-ADX entries were fine (so ADX routes the MR sleeve but does not gate momentum). New trend filters: 40d-high-more-recent-than-40d-low; 10d range expansion vs 20d ago. Kelly f = p − (1−p)/r and optimal leverage = mean/variance of returns; size DOWN as capital draws down (the LTCM lesson).

**Sinclair (Book2)** — expected value is the only metric; win-rate arguments are explicitly wrong (his Jim Rogers takedown). You must know WHY a trade has edge or you cannot tell a broken strategy from a bad run. Volatility: forecast with EWMA (λ≈0.94), exclude earnings jumps as outliers rather than letting them decay; vol spikes up and mean-reverts; use IV/vol in PERCENTILE/context terms, never absolute. Earnings are binary events — stand aside around them. Kelly/geometric growth for sizing; gambler's ruin for oversizing. Expiry pinning is real for optioned stocks (delta-hedging mechanics) but is an expiry-day effect, not a weekly-range trade. Never short vol into rising vol.

**Cohen (book1)** — the strategy catalog keyed by proficiency × direction × volatility. The matrix V10 uses: bullish + low IV → long calls / bull call spreads (cheap premium); bullish + high IV → bull PUT spread (credit, defined risk) rather than buying expensive calls; neutral + high IV → iron condor/butterfly class, always wings, never naked; ladders/ratio backspreads stay out (advanced/expert tier, unlimited-risk variants excluded by policy).

**ML4T repo (all 24 chapters reviewed)** — Ch2–3 (data sourcing/alt-data): not actionable now (no infra). Ch4+24: factor engineering — lagged returns at 1/5/10/21/63/126d, 12-1 momentum, NATR, %B, normalized MACD, plus WorldQuant alpha#101; evaluate factors by rank-IC (information coefficient), the Fundamental Law (IR ≈ IC × √breadth — with 55 names and ~weekly signals, breadth is your binding constraint; widening the universe raises capacity for alpha more than tuning weights). Ch5: Sharpe/IR + Kelly notebook; pyfolio-style evaluation. Ch6: purged/embargoed cross-validation — naive CV on overlapping financial labels is leakage; `utils.py::MultipleTimeSeriesCV` pattern ported. Ch7/11/12: the canonical workflow — engineer features → LightGBM/RF classifier on cross-sectionally ranked features → predict 5–21d outperformance → IC-check with Alphalens → only then backtest; ch12's GBM-on-daily-equities is exactly what `ml_signal.py` implements (lightweight, no zipline). Ch9: GARCH-class vol forecasting (EWMA implemented as the pragmatic subset) and cointegration/pairs — pairs trading is a future sleeve candidate, not done now. Ch10 (Bayesian Sharpe), 13 (PCA risk factors/HRP — relevant only when holding 5+ concurrent positions), 14–16 (NLP: word-list sentiment ported in lightweight form; full earnings-call NLP needs transcript data), 17–22 (deep learning/RL: data-hungry, overkill for 55 names of dailies — deliberately NOT adopted; honesty over fashion), 23: meta-advice consistent with Kestner (track live vs backtest, retire decayed edges).

## 2. Agent 1 — Technical (changes)

1. **F1 hard veto** now gates `direction` (was: only raised threshold). Evidence: report 05 — PF 1.16→1.26, expectancy ₹214→₹314, DD 15.2%→12.9%.
2. **Mean-reversion sleeve fixed** (old rule fired 0× in 92,869 stock-days): now `RSI(2) < 10 AND price > MA200 AND ADX < 32`, flagged SHADOW-ONLY with its own exit profile (target MA20, 5-day time stop, 1.5×ATR SL). Do not trade it live until shadow logs validate it.
3. **Kestner confirmations (need ≥2/3):** 40d-high more recent than 40d-low; 10d range expansion vs 20d ago; 12-1 momentum > 0.
4. **Momentum-laggard veto:** no longs with RS rank < 40 (decile study: pure momentum ranking beat the composite; bottom-half names drag).
5. **Hybrid exits (your choice):** 2×ATR initial stop → bank 50% at +3×ATR, stop to breakeven → chandelier trail (highest close − 3×ATR) on the rest → 12-day time stop only for trades that never reached +1×ATR.
6. **Sizing:** legacy `min(₹20k, 4%)/(2×ATR)` now capped by half-Kelly from live stats (reads `strategy_memory.json → live_stats`, falls back to backtest stats) and halved when VIX percentile ≥ 70.
7. **New fields:** `adx`, `rsi2`, `confirmations`, `mom_12_1_pct`, `exit_plan`, `sizing`, `ml` — all additive; existing UI keys unchanged.
8. **Regime block:** VIX now also in 1-yr percentile + 5-day slope; Nifty EWMA realized vol + percentile (Sinclair). Tier uses percentile-first logic.

## 3. Agent 2 — Fundamental (changes)

1. **News scoring** upgraded from kill-words-only to kill-words (hard veto, unchanged) + Loughran-McDonald-style positive/negative word lists → `news_score.net_score ∈ [−1,1]` and a POSITIVE/NEUTRAL/NEGATIVE label.
2. **Earnings window widened** (Sinclair: binary vol events): `earnings_risk` now T−5…T+1 (was 0–3 days), plus `earnings_caution` flag for 6–10 days out.
3. **Quality score 0–4** (ROE≥15, D/E≤1, margin≥8, revenue growth>0) as a strategist tiebreaker — never a gate (yfinance `.info` is approximate).

## 4. Agent 3 — Strategist (changes)

1. **EV-first playbook:** the prompt now carries `ev_per_R` and forbids win-rate-only justifications (Sinclair). Conviction blends score margin, confirmations, breakout volume, ML tilt, and is halved in elevated vol.
2. **Options matrix rewritten** (Cohen × Sinclair, VIX-percentile keyed): <30th pctile → ATM call / bull call spread; 30–70 → bull call spread; >70 → bull PUT spread (credit, defined risk); neutral + >70 + falling VIX → iron condor (paper only). Short premium FORBIDDEN while VIX 5d slope > 0. Weekly ±200-pt expiry fly formally RETIRED (own study: 48% hit rate, not ~80%).
3. **Sanitizer hardened:** in addition to the no-upgrade rule, it now force-vetoes BUY on earnings window, VIX EXTREME, ML prob < 0.40, and coerces short-premium structures to EQUITY/NONE when vol is rising. Hybrid `exit_plan` is always copied verbatim from Python.

## 5. New ML layer (optional, ML4T ch11/12)

`ml_signal.py` + `train_ml.py`: 19 cross-sectionally ranked features → gradient-boosted classifier (LightGBM → sklearn HGB → logistic fallback) predicting P(beat watchlist median over 5 days), purged walk-forward CV (5-day embargo), reported as per-fold AUC + daily rank-IC. Integration is read-only: `technical.ml.prob` tilts conviction ±0.15 and a <0.40 floor forces WAIT; if the model file or libraries are absent everything degrades to rules-only. **Install:** `pip install scikit-learn scipy joblib lightgbm`, then `python train_ml.py` from the recommender folder. Retrain monthly (Kestner half-life). Trust gates: mean rank-IC > 0.02 = usable; AUC > 0.60 on this data = suspect leakage, do not celebrate.

## 6. Live-pipeline patches

- `morning_scan.py`: (a) F1 hard veto on LONG verdicts; (b) MR rule replaced with the fixed RSI(2) version, marked SHADOW, direction no longer set; (c) `rsi2/ma200/adx` added to the report JSON.
- `morning_crew.py`: vol_regime bug fixed — it compared stock ATR% (~2) against VIX thresholds (12/18) so every day was "low_vix"; now uses live VIX with ATR%-based fallback.

## 7. Validation harness

`backtest_v10.py` runs the 7-year ablation: A=V9 baseline, B=+F1 veto, C=+hybrid exits only, D=V10 entries only, E=V10 full — net of your `cost_model.py` costs, with Sharpe AND K-ratio (Kestner) per variant, MR fire-counts old vs new, results to `backtest_results/v10_summary.json` + `v10_trades.csv`. Same caveats as report 05: survivorship-biased universe; relative comparisons are the signal.

## 8. Governance rules adopted (process, not code)

1. **Kill switch (Kestner ch4):** monthly, regress the live equity curve on time; if it closes below the −2×SE band, halt the sleeve and re-evaluate. (weekly_tuner is the natural home; not yet wired.)
2. **Annual re-test** of score weights (the weightage paper's Stage-3) and monthly ML retrain.
3. **Options stay paper-only** until real NSE chain history is wired (report 05 §5b stands).
4. **Shadow-first:** MR sleeve and ML floor run in shadow/advisory mode until forward data validates them.

## 9. Honesty box — what V10 does NOT claim

- Survivorship bias still flatters every backtest number; the ablation's *relative* deltas are the evidence.
- The wider earnings window and confirmation gates REDUCE trade count; with ~55 names this system will trade less often — by design (EV per trade over activity). If you want more signals, widen the universe (raises breadth/IR per the Fundamental Law) rather than loosening gates.
- The ML layer on 55 symbols × 7 years is small-data ML; expect IC in the 0.01–0.04 range at best. It is a tilt, not an oracle, and the code says so when it isn't.
- 2025–26 weakness may be regime, not noise. The F1 veto + confirmations are the defensive answer; nothing here promises the 2020-21 expectancy back.
