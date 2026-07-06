# Algo Trading System — Required Fixes, Solutions & Gemini CLI Prompts

**Document 2 of 2** | Companion: `01_System_Summary_and_Strategy.md`
**Purpose:** Every reason this system will not yet succeed, the fix for each, an exact Gemini CLI prompt where code changes are needed, and a criticality rating.

---

## How to Read This Document

Each gap has: the **problem**, the **fix**, a **criticality tag**, and where relevant a **copy-paste Gemini CLI prompt**. Criticality is defined as:

| Tag | Meaning | Timeline |
|---|---|---|
| 🔴 **URGENT** | Corrupts the data/metrics you use to decide go-live, or risks unbounded loss. Fix before logging another trade. | This week |
| 🟠 **HIGH** | Makes your results materially misleading. Fix before trusting any expectancy number. | Within 2 weeks |
| 🟡 **MEDIUM** | Improves honesty/robustness; not blocking validation. | Within a month |
| 🟢 **LOW / LATER** | Research-driven enhancements. Only after a stable baseline exists. | After 100+ closed trades |

**Important workflow note:** these are paper-trading fixes. Apply them, then let the system run on the corrected logic. Do not carry over old `paper_trades.json` P&L numbers as if they were valid — they were generated under the buggy/cost-free logic and must be treated as void.

---

## 🔴 GAP 1 — `update_performance()` computes average win/loss incorrectly

**Criticality: 🔴 URGENT — this is the single most dangerous bug in the codebase.**

**Problem.** In `position_monitor.py`, `update_performance()` sets:
- `avg_win_inr = best_trade_inr / wins`
- `avg_loss_inr = |worst_trade_inr| / losses`

This divides your *single best* trade by the *count* of wins — it is not an average at all. Every downstream metric flows from these two numbers: **expectancy, profit factor, and the Kelly position-size fraction**. This means the system could one day size real-capital positions using fundamentally wrong statistics. The deployment gate itself reads these numbers.

**Fix.** Maintain running sums of all winning and losing P&L (not just the max/min) and divide by the respective counts. Add a persistent `gross_win_sum_inr` and `gross_loss_sum_inr` to `performance_live` so averages survive across runs.

**Gemini CLI prompt:**
```
In position_monitor.py, the update_performance() function calculates avg_win_inr and avg_loss_inr incorrectly. It currently uses best_trade_inr divided by wins count, and worst_trade_inr divided by losses count, which is mathematically wrong (it divides a single max/min value by a count instead of averaging all trades).

Fix it as follows:
1. Add two new persistent fields to the performance_live section logic: "gross_win_sum_inr" and "gross_loss_sum_inr", both defaulting to 0.
2. In the per-trade loop, when pnl > 0 add pnl to gross_win_sum_inr; when pnl <= 0 add abs(pnl) to gross_loss_sum_inr.
3. Compute avg_win_inr = gross_win_sum_inr / wins (guard against division by zero), and avg_loss_inr = gross_loss_sum_inr / losses (guard against zero).
4. Keep best_trade_inr and worst_trade_inr as separate max/min trackers — do NOT use them for averages.
5. Ensure expectancy_per_trade_inr and profit_factor are recomputed from these corrected averages.
6. Also update strategy_memory.json's performance_live block to include gross_win_sum_inr: 0 and gross_loss_sum_inr: 0 so the fields exist.

Do not change any other logic. Show me a diff before writing.
```

**Validation (do this yourself, not via Gemini):** after the change, hand-compute expectancy for 3–4 fake closed trades and confirm the script produces the same number. See "What Cannot Be Done via Gemini CLI" at the end.

---

## 🔴 GAP 2 — Execution depends on an unmonitored scheduler (single point of failure)

**Criticality: 🔴 URGENT for go-live; 🟠 HIGH for paper trading.**

**Problem.** The entire exit path depends on Windows Task Scheduler firing `position_monitor.py` reliably every 30 minutes. If the machine sleeps, loses internet, or the scheduler misfires, a position silently misses its stop-loss. There is no heartbeat, no "did the monitor actually run?" check, and no alert on failure. For paper trading this corrupts data; **with real capital, a missed 3:30 PM run during a gap-down is an unbounded loss.** A trading system you cannot *prove* ran is worse than no system.

**Fix (two parts).**
1. **Heartbeat + failure alert (can be scripted):** every scheduled script appends a timestamped entry to a `heartbeat.json`; a small watchdog script checks that each expected run happened and sends a Telegram/email alert if one is missing.
2. **Move off Task Scheduler to a more reliable runner (partly manual — see end section).**

**Gemini CLI prompt (for the heartbeat layer):**
```
Create a new file heartbeat.py with two functions:
1. record_heartbeat(script_name: str) — appends {"script": script_name, "timestamp": ISO8601 now, "date": today} to heartbeat.json (create the file as an empty list if missing).
2. check_missed_runs(expected: dict) — takes a dict of {script_name: latest_expected_time_HHMM} for today, reads heartbeat.json, and returns a list of script names that have NO heartbeat entry for today after their expected time.

Then add a call to record_heartbeat("<script_name>") at the very end of the main() function in each of these files: morning_crew.py, execution_engine.py, midday_sentinel.py, position_monitor.py.

Also create watchdog.py that calls check_missed_runs() with the day's expected schedule and, if anything is missing, prints a clear alert and writes a MISSED_RUN entry to decision_log.json. Leave a clearly-marked TODO comment where a Telegram/email send would go — do not implement the actual send.

Show me the plan before writing any files.
```

---

## 🟠 GAP 3 — Paper-trading P&L is optimistic fiction (no transaction costs)

**Criticality: 🟠 HIGH — your expectancy is meaningless until this exists.**

**Problem.** There is zero modelling of STT, brokerage, exchange fees, GST, SEBI charges, or stamp duty anywhere in the P&L math. Mitra's research is explicitly about how costs kill Indian technical-rule profits. For a swing system this is roughly 0.2–0.5% round-trip on equity — enough to erase the edge of a 3:2 reward-risk setup. Every "win" you book is overstated.

**Fix.** Build one transaction-cost function and subtract it from every closed-trade P&L. Use current (2026) rates; flag the exact numbers for your own verification since statutory rates change.

> ⚠️ **Verify these rates yourself before trusting them** — STT, stamp duty, and exchange charges change by regulation and by segment (delivery vs intraday vs F&O). The structure below is correct; the exact percentages must be confirmed against your broker's contract note. I do not have a verified primary source for the precise post-April-2026 rates.

**Gemini CLI prompt:**
```
Create a new file cost_model.py with a function:

transaction_cost(segment: str, side: str, price: float, quantity: int, lot_size: int = 1) -> float

It must compute total round-trip-aware per-side charges for Indian markets. Use these placeholder rate CONSTANTS at the top of the file, each clearly commented "VERIFY against current SEBI/exchange rules":
- For EQUITY delivery: STT, exchange transaction charge, SEBI turnover fee, stamp duty (buy side only), GST on (brokerage + transaction charges), and a flat brokerage of 20 rupees or 0.03% whichever is lower.
- For OPTIONS: STT on sell side of premium, exchange transaction charge on premium, SEBI fee, stamp duty on buy side, GST, flat 20 rupee brokerage per order.

Return the rupee cost for the given side. Make every rate a named constant so I can edit them in one place.

Then integrate it into position_monitor.py check_exit() and the closing logic: when a trade closes, compute entry-side cost + exit-side cost and subtract the total from pnl BEFORE writing it to the trade record. Add a new field "costs_inr" to each closed trade and a "pnl_gross_inr" field so I can see pnl before and after costs.

Show me cost_model.py and the diff to position_monitor.py before writing.
```

---

## 🟠 GAP 4 — Fake options P&L

**Criticality: 🟠 HIGH — any options "performance" is currently meaningless.**

**Problem.** In `position_monitor.check_exit()`, options outcomes are hardcoded fantasy: target hit books `cost × 1.0` (assume doubled), SL hit books `−cost × 0.5` (assume halved), time-stop books `−cost × 0.3`. Real option P&L depends on delta, gamma, theta, and IV crush — none modelled.

**Fix.** Two honest options, pick one:
- **(Recommended now) Stop paper-trading options entirely** until you can price them. Restrict the decision matrix to EQUITY-only so your validated baseline is clean.
- **(Later) Model options properly** with an options-pricing library and a real options data feed (see Document 1 §5 and the per-stock IV gap).

**Gemini CLI prompt (for the recommended "equity-only for now" path):**
```
In strategy_memory.json, temporarily force all entries to EQUITY so we can build a clean, honest paper-trading baseline without fake options P&L.

Do this by editing the instrument_selection.decision_matrix: change the "instrument" value to "EQUITY" for these keys: score_11_14_low_vix, score_11_14_normal_vix, score_11_14_high_vix, mean_reversion_any. Keep score_9_10_any_vix as EQUITY. For short_momentum_any, set instrument to "SKIP" for now (we are not validating shorts yet — see Gap 9).

Add a top-level comment field in instrument_selection: "_note_equity_only": "Options disabled during baseline validation — fake options P&L removed. Re-enable only after options pricing model + data feed exist."

Do not touch the Python files for this change. Show me the before/after of the decision_matrix.
```

---

## 🟠 GAP 5 — Signals fire too rarely to ever validate (the sample-size trap)

**Criticality: 🟠 HIGH — this is why you have 3 trades after weeks.**

**Problem.** 3 trades in 6 days, and realistically slower. The combination of a 9–11/14 dynamic threshold, max 3 positions, and max 1 per sector chokes the system. At this rate you need *months* to reach 20 trades — and 20 is itself far too few to distinguish skill from luck (you want 100+).

**Fix — decouple signal *validation* from capital *deployment*.** Log a lightweight "shadow trade" for **every** signal above a lower threshold (e.g. 7/14) into a separate file, tracked to its theoretical SL/target/time-stop, even when you deploy no capital. This multiplies your learning data without risking anything, and lets you validate the scoring system in weeks. You are currently discarding ~95% of your learning signal by only recording the 3 trades you actually sized.

**Gemini CLI prompt:**
```
Add a shadow-logging system that records EVERY signal for validation, separate from real (paper) capital deployment.

1. In morning_scan.py, after the full scan is complete, write a new file shadow_signals.json: append an entry for every stock whose score >= 7 (not just >= 9), with fields: date, symbol, score, strategy, direction, price, atr_sl, atr_target, and status "SHADOW_OPEN". Do not let shadow logging interfere with the existing scan_*.json output or the >=9 deployment path.

2. Create shadow_monitor.py (modeled on position_monitor.py's check_exit math, EQUITY logic only, including the cost_model.py costs from Gap 3) that runs daily, fetches prices for all SHADOW_OPEN entries, and closes them at theoretical SL/target/12-day time-stop, recording pnl_net_inr. It must NOT touch paper_trades.json or strategy_memory.json — shadow data stays fully isolated in shadow_signals.json.

3. Add a shadow_performance() reporting function that prints win rate, expectancy, and profit factor across all closed shadow signals, broken down by score bucket (7-8, 9-10, 11-14) and by strategy.

Show me the plan and the new file structure before writing. Reuse existing helper patterns from position_monitor.py.
```

---

## 🟡 GAP 6 — Real arithmetic/structure bugs beyond Gap 1

**Criticality: 🟡 MEDIUM (harmless today, but they signal drift).**

**Problems found:**
- **Duplicate `scan_headlines` definition** in `midday_sentinel.py` — an empty stub at line ~242, the real one at ~247. Harmless due to Python redefinition, but it is copy-paste rot.
- **Sector-map vs sector-limits mismatch:** `sector_map` uses "Cement", "Healthcare", "Utilities" but `sector_limits` has no entry for those — they silently fall back to `default: 1`. Probably fine, but unmanaged.
- **Documentation says 55 stocks; actual `active_watchlist` has 42.** Ground truth and docs disagree.

**Fix.** Clean up the duplicate, align the sector tables, reconcile the watchlist count (this connects to your small/mid-cap request below).

**Gemini CLI prompt:**
```
Three small cleanups:

1. In midday_sentinel.py there are two definitions of scan_headlines() — an empty/incomplete stub appears right before the real implementation. Remove the stub so only the complete function remains. Verify the file still parses.

2. In strategy_memory.json, the portfolio_rules.sector_limits table is missing entries for sectors that DO appear in sector_map: "Cement", "Healthcare", "Utilities". Add each with a limit of 1, matching the existing style.

3. Print the current count of stocks in stock_universe.active_watchlist and confirm whether it is 42 or 55, so we can reconcile it with the documentation.

Show me each change as a diff. Do not modify any trading logic.
```

---

## 🟡 GAP 7 — Survivorship & look-ahead bias in the universe

**Criticality: 🟡 MEDIUM — caps how much you can trust forward-test results.**

**Problem.** Your 42 stocks are today's large-cap winners. Any test on this list inherits **survivorship bias** — you are trading names that already won. RS-rank is computed only within these survivors, not against the broad market.

**Fix.** This is partly a mindset fix and partly data. You cannot fully fix survivorship without point-in-time index membership data, which is a sourcing problem, not a code problem (see end section). What you *can* do in code: compute RS-rank against a broad market proxy (e.g. a Nifty 500 ETF return) rather than only within the 42-name watchlist, which reduces the self-referential bias.

**Gemini CLI prompt:**
```
In morning_scan.py, the compute_rs_ranks() function currently ranks each stock's 12-month return only against the other stocks in the watchlist, which is self-referential. Add a market-relative benchmark:

1. Fetch the 12-month return of a broad-market proxy (use the index symbol ^CRSLDX for Nifty 500, with ^NSEI Nifty 50 as fallback if it fails).
2. For each stock, also compute "rs_vs_market" = stock_12m_return - benchmark_12m_return.
3. Keep the existing within-watchlist percentile rank, but ADD rs_vs_market as a separate field in the output dict so we can later compare which is more predictive (via the shadow system).
4. Do not change the scoring weights yet — just expose the new field.

Show me the diff before writing.
```

---

## 🟢 GAP 8 — Missing research-backed alpha (quality, PEAD, per-stock IV)

**Criticality: 🟢 LOW / LATER — do NOT build these until a stable baseline exists.**

**Problem.** No quality factor, no PEAD overlay, no per-stock IV. These are the highest-value *additions* from the research (Document 1 §5).

**Fix — but timed correctly.** Adding alpha factors before you have an honest, validated baseline just helps you overfit. The correct order is: fix measurement (Gaps 1–6) → validate baseline via shadow system → *then* layer these in one at a time, each tested in the shadow system first.

**When you are ready, start with the quality filter** (lowest effort, highest documented alpha, uses annual fundamental data not a live feed):
```
[RUN THIS ONLY AFTER 100+ CLOSED SHADOW TRADES VALIDATE THE BASELINE]
Add a quality filter as a new optional scoring category in morning_scan.py. For each stock, pull ROE, debt-to-equity, and earnings stability (you may use yfinance .info fields as a first approximation, clearly commented as needing a better data source later). Rank the watchlist into quintiles on a composite quality score. Add a "quality_score" field (0-1 points) to the output but keep it OUTPUT-ONLY at first — do not add it to the tradeable score until the shadow system shows it improves expectancy. Show me the plan first.
```

PEAD and per-stock IV require earnings-estimate and options data feeds respectively — those are sourcing tasks (see end section), not pure Gemini CLI jobs.

---

## 🟢 GAP 9 — Short and mean-reversion paths are unvalidated

**Criticality: 🟢 LOW — disable, don't debug, for now.**

**Problem.** The SHORT and MEAN_REVERSION paths add complexity and untested failure modes while you have zero closed trades on even the primary momentum path.

**Fix.** Disable both during baseline validation (the Gap 4 prompt already sets `short_momentum_any` to SKIP). Validate momentum-long first. Re-introduce one path at a time through the shadow system.

---

## Your Two Requested Changes

### Change A — Monthly profit target from 20% to 5%

**This is a correct and important change.** A 20%/month target (~790% annualised) is not ambitious, it is a category error that pushes toward oversized positions and revenge-overtrading. 5%/month is still aggressive but is at least in the realm of the possible for a disciplined swing system, and — crucially — you should judge the system on **risk-adjusted** terms (Sharpe, max drawdown), not the headline return.

**Gemini CLI prompt:**
```
In strategy_memory.json, change the monthly profit target from 20% to 5%, consistently:
1. performance_live.month_target_pct: change 20.0 to 5.0
2. performance_live.month_target_inr: change 100000 to 25000 (5% of 500000 capital)
3. meta.deployment_criteria.target_monthly_return_pct: change 20.0 to 5.0
4. Update any note/string that references the old 20% or 1,00,000 target to reflect 5% / 25,000.

Search all .py files for hardcoded references to 100000, "20%", or month_target and report any that also need updating (do not change Python logic without showing me first).

Show me every change as a diff.
```

### Change B — Add small-caps and mid-caps to paper trading

**Sensible — small/mid-caps are where the research says technical and momentum edges are often *stronger* (greater information frictions). But two cautions:** (1) small-caps have wider spreads and higher impact cost, so your cost model (Gap 3) and slippage tiers become *more* important, not less; (2) liquidity screening is essential — illiquid names will give you fills in backtest that you could never get live.

**Gemini CLI prompt:**
```
Expand the watchlist to include mid-cap and small-cap NSE stocks for paper trading, with proper liquidity safeguards.

1. In watchlist.json (and the watchlist_reader.py loader if needed), add a "cap_tier" field per stock: "LARGE", "MID", or "SMALL". Tag existing 42 as LARGE.
2. Add a curated set of liquid mid-cap and small-cap NSE names (only names with consistent high daily turnover — exclude anything thinly traded). For each, include its sector, Angel One token, lot size, and liquidity tier (MIDCAP or SMALLCAP) consistent with the existing SLIPPAGE_PCT tiers in execution_engine.py and position_monitor.py.
3. In morning_scan.py, add a minimum-liquidity gate: skip any stock whose 20-day average traded value (close * volume) is below a configurable threshold (default 50000000 rupees / 5 crore). Make the threshold a constant.
4. Confirm the SLIPPAGE_PCT dict already handles SMALLCAP at 0.004 — small-caps must use the higher slippage tier.
5. Ensure sector_map and sector_limits in strategy_memory.json cover any new sectors introduced.

Before writing, show me: the proposed list of new mid/small-cap symbols, and confirm each has an Angel One token and lot size. Flag any symbol where you are not certain of the token or lot size so I can verify it manually.
```

> ⚠️ **You must verify the Angel One tokens and lot sizes yourself** for any new symbols. A wrong instrument token silently fetches the wrong price or fails — this is a data-correctness issue Gemini cannot confirm. See the end section.

---

## Recommended Fix Order (do not skip the sequence)

1. **🔴 Gap 1** — fix the average-win/loss bug + write a test (this week).
2. **🔴 Gap 2** — heartbeat + failure alerts (this week).
3. **🟠 Gap 3** — transaction-cost model (before trusting any number).
4. **🟠 Gap 4** — disable fake options P&L (equity-only baseline).
5. **🟠 Gap 5** — shadow-logging for sample size.
6. **Change A + B** — 5% target, add small/mid-caps with liquidity gate.
7. **🟡 Gaps 6, 7** — cleanups + market-relative RS.
8. **🟢 Gaps 8, 9** — research alpha & extra strategy paths, ONLY after 100+ shadow trades validate the baseline.

---

## What CANNOT Be Done via Gemini CLI

These require you, a data subscription, or infrastructure outside the code editor. Gemini can scaffold them but cannot complete them:

1. **Unit-test verification of the performance math (Gap 1).** Gemini can *write* a test, but **you** must hand-compute the expected expectancy for a few trades and confirm the output matches. Trusting Gemini to both write the fix and certify its own fix is how silent errors survive. Do this verification manually.

2. **Replacing Windows Task Scheduler with a reliable runner (Gap 2).** The heartbeat is scriptable; migrating the actual scheduling to something robust (a cloud VM with cron + a process supervisor, or a managed scheduler) is an infrastructure decision and setup task. It involves hosting, credentials, and uptime — not code Gemini can write into your repo.

3. **The actual alert delivery (Gap 2).** Wiring a real Telegram bot or SMTP email requires you to create the bot/token/app-password and store secrets in `.env`. Gemini leaves the TODO; you provision the channel.

4. **Verifying statutory transaction-cost rates (Gap 3).** The exact STT, stamp-duty, exchange, and GST percentages change by regulation and segment. You must confirm them against a real contract note. Gemini will use placeholders.

5. **Real options pricing + options data feed (Gaps 4, 8).** Honest options P&L needs live option-chain data (IV, greeks) from a paid feed and a pricing model calibrated to it. This is a data-subscription and modelling project, not an editor task.

6. **Earnings-estimate data for PEAD; fundamental data for the quality factor (Gap 8).** Reliable SUE and quality scoring need a fundamentals/estimates data source. `yfinance` is a rough approximation at best; a real implementation needs a proper provider.

7. **Point-in-time index membership to fix survivorship bias (Gap 7).** Eliminating survivorship bias properly requires historical constituent data (what was *in* the index on each past date). This is specialised, usually-paid data.

8. **Angel One instrument-token and lot-size verification for new symbols (Change B).** A wrong token fails silently or fetches the wrong instrument. You must verify each against Angel One's instrument master. This is a correctness check only you can sign off.

9. **Live order placement, order-status polling, and state reconciliation.** The entire real-capital execution layer — marketable limit orders, broker order-ID tracking, partial-fill handling, reconciliation against broker positions — is a careful build that touches real money. Gemini can draft pieces, but architecting and testing this safely is a deliberate engineering effort with real risk, and should not be rushed through a CLI prompt.

---

## Closing Note

The infrastructure here is genuinely well-built: keeping the LLM out of the math, the gap-rejection logic, the circuit breakers, the regime gate. The problem was never the strategy. It is that the system has not yet *proven* it can make a rupee net of costs with provable consistency — because the measurement layer was dishonest (no costs, fake options, buggy averages) and the sample size is tiny. Fix the measurement first. Validate on shadow data. Then, and only then, add the research alpha. In that order, this becomes a system worth putting money behind.
