# Backtest Report — V9 System, Index Strategies, Options Studies

**Document 5** | Step 3 | Run 2026-06-10 | Data: yfinance daily (auto-adjusted), 2018-06-01 → 2026-06-10; trading window 2019-06-03 → 2026-06-10 (252-bar indicator warm-up)
**Artifacts:** `backtest_results/trades_net.csv`, `trades_veto.csv`, `summary.json` (engine code embedded in `engine.py`, `variants.py`, `index_opt.py`)

## 0. Methodology & honesty box (read first)

- **Faithful to the code, not the docs:** rules replicated from `morning_scan.py`/`morning_crew.py` as they actually are (incl. dynamic threshold 11/10/9, no F1 hard veto, sector cap 1, max 3 positions, ₹20k risk/trade, gap-rejection >1.5% chase / >5% gap-down, slippage tiers, 12-day time stop, 2×ATR SL, 3×ATR target).
- **No lookahead:** signal on bar *t* close → fill at bar *t+1* open + slippage. SL checked before target on the same bar (pessimistic). Verified: cost model matches hand calculation to the paisa (₹76.42 buy / ₹74.39 sell on the HINDALCO example = 0.305% round trip); trade P&L re-derived independently — OK.
- **Costs:** your `cost_model.py` (entry+exit) + tier slippage both sides on every trade.
- **Caveats that cap confidence:**
  1. **Survivorship bias** — today's 55-name watchlist tested on its own past. Inflates absolute returns AND especially inflates anything momentum-ranked. Treat *relative* comparisons as more reliable than absolute numbers.
  2. **Hindsight** — V9's rules were designed in 2026 knowing this history. This is an in-sample validation of the design, not proof of forward edge. The shadow system (Gap 5) is the real validator.
  3. Claude's news veto can't be simulated — backtest is "no-veto"; live should only be safer.
  4. yfinance data quality (splits/adjustments) unverified against exchange data.

## 1. V9 equity momentum system — headline (net of costs)

| Metric | As coded | With F1 hard veto* | Fixed threshold 9 |
|---|---|---|---|
| Trades (7 yrs) | 561 | 426 | 603 |
| Win rate | 49.7% | **51.6%** | 49.4% |
| Expectancy/trade | ₹214 | **₹314** | ₹227 |
| Profit factor | 1.16 | **1.26** | 1.17 |
| Total P&L | ₹119,898 (+24.0%) | ₹133,738 (+26.7%) | ₹137,145 |
| Max drawdown | −₹75,912 (15.2%) | **−₹64,576 (12.9%)** | −₹59,712 |
| Total costs paid | ₹81,176 | — | — |

\* F1 hard veto = no longs when Nifty < 50dma (CLAUDE.md §4 spec — **not currently implemented in morning_scan.py**, which only raises the threshold).

**Reading:**
- The system is **net-positive and passes its own deployment gates** (win ≥45%, expectancy >0, DD ≤15% — borderline) — but it earned **+24% over 7 years vs Nifty buy-and-hold +93.6%**. As coded, it's a low-octane absolute-return sleeve, not an index-beater.
- **Costs consumed 40% of gross profit** (₹81k of ₹201k). Mitra's warning is empirically confirmed — every future tweak must be judged net.
- **Regime decides everything:** regime-0 trades averaged **−₹80** (77 trades), regime-1 **+₹475**, regime-2 **+₹115**. The F1 hard veto (delete regime-0 trades) improves every metric simultaneously. **Recommendation: implement the hard veto in `morning_scan.py` — it's already the documented spec.**
- **By year:** 2019 +21.9k · 2020 +62.4k · 2021 +34.9k · 2022 +11.9k · 2023 +21.7k · 2024 +6.1k · **2025 −16.8k · 2026 −22.3k**. The edge has been decaying and is negative in the current regime — consistent with your live experience (rare signals, nothing closing well). This is exactly the static-weights-age decay the weightage paper predicted; schedule annual re-tests.
- Exit mix: time-stops 224 (+68.6k), targets 151 (+679.9k incl. gaps), stops 186 (−628.7k incl. gap-throughs). Time-stops are mildly profitable — the 12-day rule is fine.

## 2. Signal-quality (decile) study — the weightage paper's Stage-2 test

Weekly (Fridays), top-5 by score, 20-day forward return, equal weight, no portfolio mechanics:

| Ranking | Mean 20d fwd | Annualised ≈ |
|---|---|---|
| 14-pt composite top-5 | +3.22% | ~40% |
| **Pure 12-mo momentum top-5** | **+4.17%** | **~52%** |
| Universe equal-weight | +2.38% | ~30% |

- The 14-pt score **does** select (+0.84%/20d over universe, 57% of weeks positive) — the composite has real signal.
- But **pure momentum ranking beat the composite by 0.95%/20d** on this universe. The weightage paper's own decision rule anticipated this: *"if the RS bucket alone explains most alpha, a 2-condition screen (12-mo RS + Nifty>200dma) may dominate."* **Caveat: survivorship bias flatters momentum ranking most** (today's list = past momentum winners), so do NOT rip out the composite on this evidence alone — but DO add `rs_vs_market` + momentum-rank columns to the shadow logs so live forward data can settle it.

## 3. Mean-reversion sleeve — DEAD CODE (critical finding)

Across **92,869 stock-days** (55 stocks × 7 years): `RSI(14) < 35 AND price > MA50` occurred **exactly 0 times**. The full MR condition (+ lower Bollinger touch) is unsatisfiable in practice — with Wilder RSI(14), a stock above its 50dma essentially never prints RSI < 35. **The MR sleeve has never been able to fire, in backtest or live.** Options to fix (re-test before adopting): RSI threshold 40–45, or RSI(2) < 10 (classic short-term MR), or replace `>MA50` with `>MA200` as the uptrend qualifier. Until redesigned, the sleeve is cosmetic — remove it from docs or fix the math.

## 4. Index-level strategies (Nifty, 2019–2026, 5bp slippage/side)

| Strategy | CAGR | Max DD | Notes |
|---|---|---|---|
| Buy & hold | 11.0% | ~−38% (2020) | benchmark |
| Dual Donchian 20/10 long-only | 2.4% | −19.2% | 34 trades, 38% win — halves DD, gives up ~80% of return |
| Golden-cross 50/200 switch | 3.0%/yr | −36.5% | 200dma lags too much to dodge fast crashes (2020) |

**Conclusion:** on a secular-bull index, daily-bar trend timing mostly *costs* return; its only payment is drawdown reduction (Donchian). Index timing is therefore a **risk-management overlay** (which V9 already uses as the regime gate — its best use), **not** a standalone alpha source. Don't build an index-trading sleeve from these; keep the regime gate.

## 5. Options studies

### 5a. Weekly expiry "pin" (the Gupta book's ±200-pt / ~80% claim) — **FAILS on current data**
343 weeks, 2019–2026: Thursday close within ±200 pts of prior Friday open only **48%** of weeks (median |move| = 210 pts = 1.21%). And it decays as the index level rises: 2019 65% → 2022 35% → 2024 28%. In percentage terms ±1.5% holds 59% — far from 80%. **The book's weekly fly strategy as stated would lose money today.** If pursued, it must be restated in % terms, sized to current straddle pricing, and validated on real weekly option prices.

### 5b. Volatility risk premium — synthetic monthly ATM straddle (**MEDIUM-LOW confidence**: Black-Scholes pricing with entry-day India VIX as IV; no smile, no real chain, ~costs only)
89 months: ATM premium averaged **59% rich** vs realized |payoff| (avg VIX 17.5). Short straddle: **+50 pts/month average, 64% win rate** — but worst single month **−1,935 pts** (≈ −₹1.45L/lot) and 2020 alone −2,196 pts. Long straddle is the mirror: loses on average, wins big in trend/crash years (2020 +2,196).
**Conclusions:** (1) the Indian VRP exists, consistent with the book's 2015 data and Quantpedia; (2) harvesting it NAKED is exactly the tail Donald-duck trade both books warn about; (3) the only acceptable expression is **defined-risk**: iron condor / iron fly with wings, regime-filtered (skip event weeks & rising-VIX), per playbook C3/C4. This matches your existing decision-matrix instinct (`wait_period_high_vix → IRON_CONDOR`).
**To raise confidence:** real NSE option-chain history (bhavcopy archives via NSE; or paid feed) → re-run with actual premiums, then paper-trade the structure in the shadow system. Do not enable options on synthetic evidence.

## 6. What changes as a result (ordered)

1. **Implement the F1 hard veto** in `morning_scan.py` (spec already says so; backtest says +₹100/trade, −2.3pp DD).
2. **Fix or retire the MR sleeve** (dead code; pick a new condition set and shadow-test it).
3. **Add momentum-rank + `rs_vs_market` columns to shadow logging** to adjudicate composite-vs-momentum on live data.
4. Keep the 12-day time stop, ATR exits, gap rejection, sector caps — all behaved sensibly.
5. Treat 2025–26 weakness as real: do not deploy more capital on the equity sleeve until shadow data shows the edge returning (or until the score is re-weighted per the paper's Stage-3 with current data).
6. Options: source real chain history before any backtest claims; until then the condor/fly playbook stays paper-only.

## 7. Verification log (Task: math checks)
- `cost_model.transaction_cost` vs hand computation: BUY ₹76.42 = ₹76.42 ✓, SELL ₹74.39 = ₹74.39 ✓ (0.305% round trip on the example).
- Engine P&L recomputed independently for sample trades ✓ (₹−2,947.65 vs −2,947.62, rounding).
- No-lookahead check: all indicators computed on bars ≤ t; fills strictly at t+1 open; same-bar SL-before-target pessimism ✓.
- Data sanity: 58/58 tickers downloaded, ≥500 bars each; Nifty 12,089 → 23,403 over the window (+93.6%) matches public record.
