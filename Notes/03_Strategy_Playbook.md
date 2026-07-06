# Strategy Playbook — Consolidated from All Notes, Books & Research

**Document 3** | Companions: `01_System_Summary_and_Strategy.md`, `02_Required_Fixes_and_Gemini_Prompts.md`, `CLAUDE.md`
**Sources (all read in full):**
- `UpdatedLogic21stMay.pdf` (V9 change log, 9p)
- `ImplementationSummary.pdf` (6p — mirrors doc 01)
- `ResearchPaper on Weightage distribution.pdf` (10p)
- *Option Greeks, Strategies & Backtesting in Python* — Anjana Gupta (273p, India/NSE-specific)
- *Professional Automated Trading: Theory and Practice* — E. Durenard, Wiley 2013 (382p)
- Published research already embedded in docs 01/02 (Jegadeesh-Titman, George-Hwang, Minervini, O'Neil, Agarwalla-Jacob-Varma, Mitra)

**Status of this document:** strategy reference for (a) the daily swing engine, (b) the options overlay, (c) the recommendation app's "Claude brain", (d) backtest design. Not financial advice. Rates/lot sizes must be verified against current NSE/SEBI circulars before live use.

---

## PART A — The Equity Swing Engine (current V9 core, validated by research)

### A1. The 14-point score (immutable contract, per CLAUDE.md)

| Bucket | Pts | Conditions | Research basis |
|---|---|---|---|
| A. Trend Alignment | 3 | P>MA50 & MA20>MA50 (2) + both MAs rising (1) | Minervini stack; Brock-Lakonishok-LeBaron; trend = regime gate not alpha (Lempérière 2014) |
| B. RS + 52W Position | 4 | 12-mo RS top 25% (2)/top 50% (1) + within 25% of 52W high (1) + ≥30% above 52W low (1) | Jegadeesh-Titman ~1%/mo; **George-Hwang 2004: 52W-high proximity dominates** — the highest-weight bucket is correct |
| C. Oscillator | 1 | RSI(14) in [40,65] | Deliberately low weight — RSI/MACD are redundant transforms of price momentum (ML feature-importance: RSI+BB ≈ 14-15% combined) |
| D. Volume/Demand | 3 | 20d vol >1× (1) + OBV slope >0 (1) + breakout candle ≥1.5× vol if 20d-high in last 5 sessions (1) | Tsang-Chong OBV; O'Neil breakout-volume signature; vol-ratio (1d) and OBV (20d) measure different horizons — keep both |
| E. Volatility/Tradability | 1 | ATR/P ≥ 1.5% | Volatility's role = sizing + tradability filter, NOT alpha (both books agree) |
| F. Market Regime | 2 | Nifty>50dma (1) + Nifty>200dma & rising (1) | "Hardest-edge filter in the entire system" — O'Neil's M, Minervini stage-2; 75% of stocks follow the market |

**Thresholds:** 11–14 STRONG BUY (options overlay allowed) · 9–10 CONSIDER (equity only) · <9 WAIT.
**Hard veto:** F1=0 (Nifty < 50dma) ⇒ no longs regardless of score.
**Dynamic threshold by regime health:** 11 in 0/2 regime, 10 in 1/2, 9 in 2/2.

### A2. Parallel sleeves (never co-mingled with the trend score)
- **Mean reversion** (20% capital budget): P ≤ lower Bollinger + RSI<35 + P > 50dma (dip in uptrend, not falling knife). Disabled in Crash & Euphoria regimes. The V8 lesson: RSI 40–65 and RSI<35 are mutually exclusive — separate sleeves, never one score.
- **Short**: P below both MAs + MA20 falling + MACD bearish. Currently SKIP until long baseline validated (Gap 9).

### A3. Exits & risk (implemented, keep)
Target = entry + 3×ATR · Stop = entry − 2×ATR · time stop 12d equity / 7d options. Risk/trade = min(₹20k, 4% capital). Gap-risk cap (20% overnight gap ≤ 2% capital). Max 3 positions, 1/sector (Exchange 2), 80% deployment cap. Circuit breakers: 3 consecutive losses → 2-day pause; daily −₹15k; weekly −₹40k. VIX matrix: <18 normal · 18–22 CAUTION (no new ATM calls) · 22–24 KILL close 15:30 · ≥24 KILL now. VIX fetch fail ⇒ CAUTION, never KILL.

### A4. Research-backed upgrade queue (strict order — after measurement layer is honest and 100+ shadow trades exist)
1. **10th condition: RS-rank vs NSE-500 ≥ 75th percentile** — "highest-marginal-value addition" (weightage paper). Expose as field first, don't score it.
2. **Quality gate** (ROE, D/E, earnings stability — quintile filter): IIMA research; a single fundamental gate (e.g., trailing-4Q EPS growth >15%) typically lifts technical-screener Sharpe 20–30% in Indian backtests. Output-only first.
3. **PEAD overlay** — perfect fit for 5–12 day holds; needs earnings-estimate data.
4. **Per-stock IV/skew** for the options side (Jain-Varma-Agarwalla).
5. Betting-against-beta; overnight vol premium — later.

**Decision rules from the weightage paper (use in backtest verdict):**
- If 14-pt top-decile 20d forward return beats the 9-pt version by **<5%**, revert to a simplified 6-bucket 1-pt-each score (Trend, RS, Volume, Volatility, Regime, Confluence).
- If the RS bucket alone explains >70% of alpha, a 2-condition screen (12-mo RS top-quartile + Nifty>200dma) may dominate everything else.
- If F1/F2 binds >70% of trading days, down-weight regime to 1pt and use it as a position-size scaler instead of a gate.
- Stage-3 (later): constrained logistic-regression/LightGBM re-weighting, weights 0–3 same-sign-as-prior, rolling 5-yr window, refit every 12 months. Static weights age (post-1987 MA-rule decay) — re-test annually.

---

## PART B — Strategy Library from *Professional Automated Trading* (Durenard)

These are the codeable strategy archetypes worth holding in the library (each maps to a regime; see Part E for the swarm idea).

### B1. Trend-following family (directional: low win% 35–45, win/loss 2–3, fat RIGHT tail)
- **CBTR — dual channel breakout (Donchian):** enter on break of SLOW N-bar highest-high; exit via FAST channel. Allows flat state; reduces whipsaw; lookback N can grow with noise. The most robust trend entry known; good Nifty/BankNifty index-level candidate.
- **AMATR:** uptrend if P > (1+β)·EMA; downtrend if P < (1−β)·EMA. More timely than channels; whipsaws in congestion ⇒ gate by SNR = |EMA slope| / counter-directional volatility.
- **AMATR2 (adaptive):** track upside/downside deviations D+ and D− separately (EMAs of up/down moves); upper channel width ∝ β·D−, lower ∝ β·D+; sensitivity α(t)=αmin+(αmax−αmin)·atan(γ·SNR), state-dependent. Cleaner move ⇒ tighter tracking; after an exit, opposite-entry channel widens (anti-whipsaw).
- **SWBR — swing breakout:** volatility-scaled ratcheting stop-and-reverse: S(t)=maxP/(1+V·α) trailing; profit at P(1+V·β); reset symmetric channel when flat. Time-scale-free.

### B2. Mean-reversion family (contrarian: high win% 55–65, win/loss 0.8–1.2, fat LEFT tail)
- **SWMR — swing reversal:** trade *inside* the volatility box (buy lower-band breach, stop at further breakout, reverse at ratchet). Counter-part of SWBR.
- **RPMR — range projection (intraday):** project tomorrow's channel: CC=Open+(S_i−S_{i−N+1})/N; CH/CL=CC·(1±TR/2); buy CL/sell CH as limit orders; stops at ±2/3·TR; exit at close.
- **ACMR — acceleration fade:** Shadow Index SI = avg pairwise bar overlap / total range (0 = gappy trend, 1 = stall); de-trended DSI low ⇒ acceleration in progress; when DSI<K2 and event-time AMA turns, counter-trade it; SL at the extreme, target a retracement fraction, time-stop. Accelerations "end in tears"; equities fall faster than they rise — long and short parameters must differ.
- **ORB — opening range breakout:** valid where a market truly closes overnight (true for NSE) — overnight information discharges at the open.

### B3. Principles to import into Diptanshu's stack (highest value per effort)
1. **FSM completeness:** every position/order manager is a finite-state machine with a *complete* transition matrix (complementary strict/non-strict inequalities; exactly one transition true per event) + defined gap-recovery behavior (ignore gap, or backfill then proceed). This kills "undefined state" bugs — the class of bug that crashed `morning_crew` and `midday_sentinel`.
2. **Same code for backtest and live** (event-driven): eliminates rewrite risk. Current backtest_v8 vs morning_scan duplication violates this — unify scoring into one module both import.
3. **Pessimistic execution:** decisions on bar t fill at bar t+1 (next open). Never same-bar fills. (Already CLAUDE.md rule 3 — enforce in every backtest.)
4. **Slippage as a function**, per security & order type; for breakout systems, average slippage UNDERESTIMATES cost (breakouts co-occur with volatility/liquidity bursts) — use higher tier on breakout fills.
5. **Costs:** nondeterministic costs (spread, impact, latency, missed trades) usually EXCEED deterministic fees. Model both; impact ∝ size/ADV.
6. **Fitness Feedback Control (FFC):** winning/losing trades cluster (regime persistence) ⇒ pause a strategy when its rolling fitness (e.g., RTNAV = NAV −(1+λ)·EMA(NAV), 10–20-trade lookback) goes negative, resume when it recovers, with hysteresis gap > one average trade's fitness. Tested positive across 500 strategies × 200 paths. Cheap, powerful — candidate for `weekly_tuner`.
7. **Regime = strategy performance:** "my TF strategy is profitable" IS the definition of a trending regime. The 5-regime classifier can be cross-checked against rolling sleeve performance.
8. **Portfolio aggregation nets internal trades** (one agent buys, another sells ⇒ book transfer, no market order) — relevant once multiple sleeves run simultaneously.
9. **GPFC — global circuit breaker on portfolio PL volatility bands**, subsuming per-strategy controls; cut losers first, let winners run.
10. **Ops rules:** never trade on stale acks (queue events while order unconfirmed); aggressive orders = IOC with limit caps; order working time ≪ signal interval; error ledger for busted trades (don't recompute agent states); "a jam of logs is better than a logjam of errors"; **when in doubt, get out** — reduce risk on any computational/data tension; panic button always reachable; release new strategies at minimal size (the market reacts to you — untestable offline).
11. **Bootstrap stress-testing:** classify daily returns into tail−/body−/body+/tail+ bins; preserve the symbolic sequence (direction & amplitude clustering), redraw values within bins ⇒ synthetic paths with realistic clustering for parameter-robustness tests. Look for performance PLATEAUS in parameter heat maps, never point optima.
12. **Overfitting taxonomy:** (1) regimes change — adapt or decay gracefully; (2) ops/discipline failures — engineer them away; (3) the market adapts to you — irreducible, stay small.

---

## PART C — Options Playbook from *Option Greeks, Strategies & Backtesting in Python* (Gupta — NSE-specific)

### C1. India market mechanics (verify rates before live)
- All NSE/BSE/MCX options are **European**. Index/currency options cash-settled; **stock options physically delivered** ⇒ the expiry-week force-exit rule for stock options is mandatory, and never hold stock straddles/strangles to expiry (delivery obligation on the ITM leg).
- SEBI margin framework (June 2020): hedged/spread positions get up to ~60–70% margin benefit vs naked ⇒ spreads are structurally capital-efficient. Sell-side margin ≈ futures margin.
- Costs (book's circa-2020 figures — VERIFY current): futures STT ₹1000/crore sell side; stamp ₹100/crore both sides; SEBI ₹15/crore; options STT on sell-side premium (and limited-to-ITM-amount on exercise); + exchange txn charges + GST. Currency derivatives cheaper (no STT/CTT).
- OI structure: highest call-OI strike = resistance; highest put-OI strike = support (add weekly strikes). Use for strike selection and S/R context.

### C2. Greeks — operating rules of thumb
- Delta≈P(ITM); ATM≈0.5; |call Δ|+|put Δ|=1. Time↓ ⇒ OTM deltas→0, ITM→1. IV↑ ⇒ all deltas→0.5.
- Gamma: max ATM, explodes near expiry (ATM 1-day ≈ 10× the 20-day value); helps buyers, kills hedged sellers near expiry. If selling+delta-hedging, prefer OTM strikes; avoid ATM gamma in the last week.
- Theta: ATM Nifty (8% IV): ~2.3/day at 80 DTE → ~4.9/day at 5 DTE. Long options are always short theta.
- Vega: max ATM, grows with DTE (ATM 2d: ~3 vs 40d: ~13 per IV point). Far-month = vega instrument; near-month = theta/gamma instrument.
- Skew: lower strikes trade at higher IV (puts expensive). Edge: buy the lower-IV strike, sell the higher-IV strike inside a structure.
- Delta-hedged P&L identity: seller earns theta but loses to gamma whipsaw (buy-high/sell-low rebalances); buyer pays theta, earns gamma scalps. Profit ⇔ realized vol vs implied vol. Hedge more frequently (30–40 pts vs EOD) ⇒ smaller hedging losses.

### C3. Structure selector — the IV × Direction matrix (cornerstone for the Claude brain)

| View ↓ / IV → | **LOW IV** | **NEUTRAL IV** | **HIGH IV** |
|---|---|---|---|
| **Bullish** | Buy calls · bull verticals (buy ATM, sell OTM) · call backspreads | Buy underlying/futures | Bull verticals (buy ITM, sell ATM) · sell puts (only as spread) |
| **Neutral** | **Buy** straddles/strangles · sell ATM butterflies · buy ATM calendars | Do nothing | **Sell** straddles/strangles (only wing-protected = iron fly/condor) · **buy** ATM butterflies · sell calendars |
| **Bearish** | Buy puts · bear verticals (buy ATM, sell ITM) · put backspreads | Sell underlying/futures | Bear verticals (buy OTM, sell ATM) · ratio verticals |

Plus the book's iron law: **never naked**. Every short option gets a cheaper long wing. Bull/bear spreads beat naked futures/options on margin, tail risk, and round-trip cost.

### C4. Structures reference (NSE-flavoured)
- **Verticals:** bull call (debit) / bull put (credit) / bear call (credit) / bear put (debit). Max P/L = strike gap ∓ premium. Nifty puts carry higher IV (skew) — credit-put spreads collect it.
- **Covered call / collar:** income on held stock; collar (buy OTM put + sell OTM call) locks unrealized gains — width trades protection vs upside.
- **Calendar:** sell near, buy far, same strike (debit; theta+, vega+). Max profit if expiry pins the strike. Caveat: assumes far-month IV holds.
- **Straddle/strangle:** REGIME-DEPENDENT (book's own backtests, monthly hold-to-expiry: 2015 long straddle −775 pts/yr; short +772; but 2018 long +503 — trending years pay buyers, range years pay sellers). Strangle = cheaper, lower gamma, better for delta-hedgers.
- **Short straddle + daily delta hedge (2015 data):** profit ₹2.58L vs ₹5.79L unhedged, but worst month improves from −₹2.88L to −₹1.17L — hedging halves tail risk for ~55% of the profit.
- **Rolling/shifting:** keep the sold structure ATM by rolling strikes as the underlying moves (Mar-2019: static −328 vs rolled +11). Applies to all structures — core adjustment discipline.
- **Butterfly:** defined-risk pin trade; value ∈ [0, gap]; ATM fly most expensive; prices crawl early, explode in last 7–8 sessions (cheap early entry, fast risk late). Buying a fly = buying a range. Pros run 2+ flies at different strikes to widen the zone.
- **Iron butterfly / iron condor:** payoff ≡ fly/condor; choose whichever is priced better. Iron condor = THE income structure: sell OTM strangle at max-OI strikes, buy further wings.
- **Ratio spread (1×2, 1×3):** credit/cheap; max profit at short strike; **unbounded tail** — the book shows pros run Nifty ratios ~400-pt OTM calls (sell 3× at 500–600 OTM) and puts at ~600 pts, shifted with the underlying, earning theta + IV drop — and explicitly warns retail off them. Backtests: +287 (2017), +280 (2018) — but one 1000-pt month can lose >400. Asymmetric: skip, or only with hard exit rules.
- **Ratio backspread (sell 1 near, buy 2 far):** wants a BIG move; defined max loss; BankNifty monthly backtest +482 (2018), +4558 (2019 — trend year). Bleeds in slow drift. Crash/Euphoria-regime tool.
- **Parity toolkit:** 6 synthetic identities; conversion/reversal; box = interest-rate trade (pros only); guts, jelly roll, diagonals, strips (1C+2P, bearish-vol), straps (2C+1P, bullish-vol), ladders.

### C5. Tradeable patterns found in the book's own data (candidate strategies to re-validate)
1. **Weekly expiry pin:** Nifty on Thursday expiry stayed within ±200 pts of previous Friday's open ~80% of the time (book's observation period) ⇒ buying cheap ATM call-fly + put-fly weekly was profitable. Re-test on current data before use.
2. **Post-event IV crush:** after results, IV drops hard (Kotak May-2020: 65→40; M&M Jun-2020: 65→40). Trade: iron condor selling the max-OI strikes after the event, wings 20–40 pts further. Risk≈reward ~1:1 with elevated win probability.
3. **Quantpedia volatility-risk-premium:** monthly sell ATM straddle + buy 15%-OTM puts as crash insurance — the systematic version of C4's income trades.
4. **Author's hybrid signal:** RSI(21)>50 on Monthly+Weekly+Daily AND 13/35 MA cross up ⇒ buy ITM call + sell ATM call (vertical), mirror for bearish — i.e., technical signal chooses direction, spread expresses it. Direct fit for the recommendation app.

### C6. Mapping to the V9 decision matrix
Current matrix (score 11–14: ATM call low VIX / call spread elevated VIX; iron condor in wait/high-VIX bucket) is **consistent with the IV matrix in C3**: momentum signal + low IV ⇒ long calls/call spreads is exactly "bullish + low IV". Two refinements the books justify:
1. Use **bull call spread by default** instead of naked ATM call when IV percentile > ~50 (cheaper theta bill, margin benefit), keep ATM call only for low-IV strong signals.
2. Add **per-stock IV rank** (computed from option chain or mibian/py_vollib implied vol history) before choosing instrument — index VIX alone misprices stock options (book's SunPharma-vs-IndusInd example: same price, IV 75 vs 238).

---

## PART D — Backtesting Standards (binding for Step 3)

1. **Walk-forward only** (CLAUDE.md rule 5). No single in-sample fits. Recommended split: fit-free rules anyway; use rolling windows for any tuned parameter.
2. **Universe & period (per weightage paper):** broadest available NSE list (NSE-500 proxy if data permits; else current watchlist with the survivorship caveat stated), daily bars, ≥5 years (2020–2025 minimum; longer if data clean).
3. **Methodology:** decile-rank by score, equal-weight top decile, 20-day forward return, weekly rebalance; benchmarks = 9-pt equal-weight version, Nifty-500 buy-hold, single-factor 12-mo momentum top-decile. Metrics: hit rate, mean 20d fwd return, Sharpe, max DD, profit factor, expectancy net of costs.
4. **Costs every trade:** cost_model.py + slippage tiers (LARGE 0.001 / MID 0.002 / SMALL 0.004 of price, breakout fills one tier worse) + STT/stamp/exchange/GST. Mitra's warning is the null hypothesis: assume costs kill the edge until proven otherwise.
5. **No lookahead:** decisions at close t, fills at open t+1. Mandatory no-lookahead unit test (score at t unchanged when future bars appended).
6. **Survivorship honesty:** today's watchlist = today's winners. Add `rs_vs_market` (vs ^CRSLDX / ^NSEI) to reduce self-reference; state the bias in every report.
7. **Options backtests:** NSE bhavcopy/option-chain history where available; otherwise synthetic pricing (Black-Scholes via mibian/py_vollib with historical IV proxies) — label every synthetic result as LOW CONFIDENCE. The fake-options-P&L lesson (Gap 4) applies to backtests too.
8. **Robustness:** parameter heat maps (plateaus not peaks); bootstrap resampled paths (B3.11); per-regime breakdown (bull/bear/sideways years separately — the straddle 2015-vs-2018 flip is the case study).
9. **Sample size:** 100+ trades per claim. Shadow-log everything ≥7/14 to multiply learning data.

---

## PART E — Architecture Roadmap (Brain → Allocation → Safety → Broker → Dashboard)

Near-term (recommendation app, Step 4): 3-agent pipeline per stock —
1. **Technical agent** (pure Python): 14-pt score + sleeve signals + ATR levels + OI S/R + IV rank. Deterministic, no LLM.
2. **Fundamental/news agent**: news headlines, kill-word scan, earnings calendar proximity, basic quality stats (ROE, D/E), shareholding red flags. Claude reads/classifies language ONLY.
3. **Strategist (Claude brain)**: receives both agents' structured JSON; applies this playbook (C3 matrix + A1 thresholds + regime); returns strict JSON: action (BUY/SELL/AVOID/WAIT), instrument (equity/spread type), entry zone, target (3×ATR or structure max-profit), stop (2×ATR or structure max-loss), reasons-for (3), reasons-against (2-3), confidence. Claude can veto/structure, never up-vote a low score and never do arithmetic — all numbers computed by Python and passed in.

Medium-term: swarm-lite — run all sleeves in shadow always; FFC per sleeve; weekly_tuner reallocates using rolling fitness; GPFC over the book. Long-term: live execution per CLAUDE.md gates (walk-forward positive net of costs, 2-week paper match, kill-switch tests).

---

## PART F — One-page cheat sheet (for the Claude brain prompt)

```
REGIME (Nifty vs 50/200dma + VIX tier + 5-state) gates everything.
  CRASH: no longs. EUPHORIA: defined-risk only, 50% exposure cap.
EQUITY SIGNAL: 14-pt score. ≥11 strong, 9-10 consider, <9 wait. F1=0 ⇒ veto.
  MR sleeve: BB-lower + RSI<35 + >50dma (20% budget, off in crash/euphoria).
EXITS: +3×ATR target, −2×ATR stop, 12d time stop. Risk min(₹20k, 4%).
OPTIONS: never naked. IV-rank × direction matrix picks structure:
  low IV + signal ⇒ buy options/debit spreads & calendars;
  high IV + signal ⇒ credit spreads;
  high IV + neutral ⇒ iron condor at max-OI strikes (post-event IV crush is the prime setup);
  low IV + neutral expecting move ⇒ long straddle/strangle (trend years only).
  Stock options: exit before expiry week (physical delivery).
NEWS: kill-words (earnings miss, guidance cut, fpi selling, margin call, credit watch...) ⇒ veto/exit.
COSTS: every recommendation states net-of-cost expectancy; spread > naked.
HONESTY: state reasons-against; state confidence; state what would invalidate the call.
```
