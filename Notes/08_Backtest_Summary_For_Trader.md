# V10 Backtest — Trader's Summary

**Document 8** | Run date 2026-06-11 | Data: yfinance daily, ~8 years (trading window Jun 2019 – Jun 2026) | Capital ₹5,00,000 | All results NET of your real cost model + slippage | Raw files: `backtest_results/v10_summary.json`, `v10_trades.csv` | Re-run anytime: `python backtest_v10.py`

---

## 1. The one-paragraph verdict

The old system (V9) made ₹1.15 for every ₹1 it lost, gave back 15% of capital in its worst stretch, and has been losing money since 2025. The upgraded system (V10) made **₹2.46 for every ₹1 lost**, cut the worst drawdown to under 5% of capital, and was profitable in 2025 and 2026 — the years that were bleeding. It does this by trading **less** (about 20 positions a year instead of 80), only in friendly markets, and by letting winners run instead of capping them. The cost bill dropped 75% (₹81.9k → ₹20.5k) because fewer, longer trades means fewer brokerage/STT hits.

---

## 2. Scoreboard — old vs new (same 7 years, same stocks, same costs)

| What you care about | V9 (old) | V10 (new) | In plain words |
|---|---|---|---|
| Profit factor | 1.15 | **2.46** | Rupees won per rupee lost. Below ~1.25 is barely surviving costs; 2+ is a healthy edge. |
| Total P&L | ₹1.15L | **₹2.68L** | +23% vs +54% on ₹5L over 7 years (un-compounded). |
| Worst drawdown | −₹75,009 (15%) | **−₹23,988 (4.8%)** | The deepest peak-to-trough hole in the equity curve. This is what you must be able to sit through. |
| Expectancy / exit | ₹203 | **₹1,320** | Average profit each time the system closes (part of) a trade. |
| Win rate (exits) | 48.9% | 57.1% | See section 4 — win rate is the least important number here. |
| Sharpe ratio | 0.90 | **3.17** | Return per unit of daily P&L volatility. >1 acceptable, >2 strong (in-sample). |
| K-ratio | 0.054 | **0.172** | How straight and steady the equity curve climbs (Kestner's measure). Higher = smoother ride. |
| Total costs paid | ₹81,875 | **₹20,456** | Costs ate 40% of V9's gross profit. V10 trades less, pays less. |

**Honesty note:** these are simulated numbers on today's watchlist tested on its own past (survivorship bias) — absolute profits are flattered. What you can trust is the *relative* improvement, because both systems were tested on identical data with identical costs.

---

## 3. Where the improvement comes from (each change tested alone)

| Step | Trades | PF | Exp/exit | Max DD | Sharpe | What it proves |
|---|---|---|---|---|---|---|
| A. V9 as it was | 566 | 1.15 | ₹203 | −₹75k | 0.90 | Baseline (replicates report 05). |
| B. + F1 hard veto only | 420 | 1.33 | ₹398 | −₹58k | 1.97 | Refusing to buy when Nifty < 50dma deletes the worst trades. |
| C. + new exits only | 283 | 1.93 | ₹952 | −₹34k | 2.56 | **Biggest single win.** Letting winners trail instead of capping at 3×ATR. |
| D. + stricter entries only | 422 | 1.41 | ₹488 | −₹52k | 2.41 | Confirmation filters + momentum-laggard veto add steadiness. |
| E. Everything (V10) | 203 | **2.46** | **₹1,320** | **−₹24k** | **3.17** | The combination compounds — fewer, better trades, held smarter. |

---

## 4. Read this before judging it live: win rate vs payoff

Per **position** (entry to fully flat), V10 won only **47.5%** of the time. That is by design, and it is the part that will feel uncomfortable:

- Average winning position: **+₹6,801**
- Average losing position: **−₹2,478**
- Payoff ratio: **2.7 : 1** — winners are nearly 3× the size of losers
- Worst single position in 7 years: **−₹5,654**. Best: **+₹85,855** (a trailed runner)

So the system loses slightly more often than it wins, but losses are small and capped while winners are open-ended. Expect losing streaks: at ~48% win rate, **5 losses in a row is normal and will happen several times a year** (≈ −₹10–13k at backtest sizing). That is not the system breaking — that is the system working. The thing that pays you is the occasional trade you hold for 2–5 months while the trail ratchets up.

(The 57.1% "win rate" in the scoreboard counts exit events — banking half at target counts as a win even if the runner later stops at breakeven. Per-position is the honest number: 47.5%.)

---

## 5. How a typical trade now behaves

About **20 positions per year** (range 13–39), average hold **24 days**, median 14, longest 173.

1. **Entry** — score ≥ threshold, Nifty above its 50dma, ≥2 of 3 trend confirmations, stock in the top 60% by 12-month strength. Filled next morning at open (gaps >1.5% are skipped).
2. **Initial stop** — 2×ATR below entry. This is the most you intend to lose.
3. **At +3×ATR** — sell HALF, move the stop on the rest to breakeven. From here the trade cannot lose money. This happened on **46% of positions**.
4. **The runner** — trailed by a chandelier stop (highest close since entry minus 3×ATR). In trends this holds for months.
5. **Dead-money rule** — if the trade never even reached +1×ATR by day 12, exit. Only 12 trades in 7 years died this way (−₹16.5k total) — cheap insurance.

The exit ledger tells the story: in V9, stop-loss exits cost **−₹5.86L** in aggregate. In V10, "stop" exits *made* **+₹1.36L** — because most stops that get hit are chandelier trails locking in profit, not initial losses.

---

## 6. Year by year — when it wins, when it bleeds

| Year | V9 (old) | V10 (new) | Market character |
|---|---|---|---|
| 2019 | −₹3.9k | −₹8.6k | Choppy — trailing exits give back in chop. Worst case for V10's style. |
| 2020 | +₹92.7k | +₹67.7k | Crash + V-recovery — both fine; V10 enters later (regime gate). |
| 2021 | +₹19.5k | **+₹121.5k** | Strong trend — exactly what the trail is built for. |
| 2022 | −₹8.2k | −₹12.2k | Sideways/down — regime gate kept it mostly out; small bleed. |
| 2023 | +₹18.1k | +₹33.1k | Decent trend year. |
| 2024 | +₹41.1k | +₹42.3k | Comparable. |
| 2025 | **−₹29.4k** | **+₹1.2k** | The year V9 broke. V10 roughly flat — capital preserved. |
| 2026 YTD | **−₹14.9k** | **+₹23.0k** | The fix is visible in the current regime. |

Pattern to internalise: **V10 is a trend-capture system.** It prints money in trending years (2021), stays roughly flat in hostile years (2022, 2025), and pays a small "chop tax" in directionless tape (2019). If the next 7 years have no trends, it will grind sideways — but it should no longer dig the −15% holes.

---

## 7. Sizing reality check

In the backtest, position size was usually bound by the ₹50k-per-stock allocation cap, not the ₹20k risk budget — which is why the average loss was ~₹2.5k, not ₹20k. **All rupee figures scale with allocation; the percentages (PF, win rate, DD%, Sharpe) are what carry over.** If you raise allocation caps, expect proportionally bigger wins, losses, and drawdowns. The live system also halves size when India VIX is in its top 30th percentile — the backtest did not model this (it would only have reduced 2020-style swings further).

---

## 8. What would tell us it's broken (the kill switch)

Per Kestner: track the live equity curve monthly against its own regression line. **If the curve closes below the line by more than 2 standard errors, halt new entries and re-evaluate** — that is the statistical signature of a dead edge, not a normal losing streak. Also re-run `backtest_v10.py` after every quarter of live data and compare live expectancy vs backtest expectancy; live consistently under half of backtest = investigate (slippage, fills, regime).

---

## 9. Caveats — what this backtest is NOT

1. **Not a forecast.** In-sample design on a survivorship-biased watchlist; the rules were chosen knowing this history. The shadow/live log is the real test.
2. **No news veto modeled** — live should only be safer (kill-word vetoes remove some losers).
3. **yfinance data** — splits/adjustments unverified against exchange records; a few paisa per fill of disagreement is possible.
4. **ML layer not included** — these numbers are pure rules. The optional ML tilt (once trained) only vetoes/adjusts conviction; it was kept out of the backtest to avoid any look-ahead claim.
5. **Approximate figures** — every number here comes from `v10_summary.json` / `v10_trades.csv`; verify there if a figure matters to a decision.

---

## 10. Bottom line for the trader

Trade it like this: expect ~1–2 signals a month, take every one the system gives in a green regime, assume each trade risks a small, known amount, and do not judge any single trade. Judge the month by whether losses stayed small, and the year by whether a few runners paid for everything. The numbers say the edge is real after costs *in simulation*; the next 6 months of live/shadow trades decide whether it is real in the market.
