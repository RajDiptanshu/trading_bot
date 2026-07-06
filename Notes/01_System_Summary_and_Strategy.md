# Algo Trading System — Current State & Strategy Summary

**Document 1 of 2** | Companion: `02_Required_Fixes_and_Gemini_Prompts.md`
**Status:** Paper trading, zero real capital deployed
**Reviewed against:** ground-truth read of all Python scripts and JSON state files
**Last updated:** 2026-06-02 — bug fixes and Task Scheduler hardening applied (see Section 9)

---

## 1. What This System Is, In One Sentence

A **once-daily, long-biased swing-trading engine** for NSE equities that uses a deterministic 14-point scoring screener to find momentum-breakout setups, confines an LLM to a news-veto role only, and executes/monitors trades through pure-Python math with a market-regime safety gate.

In classical terms: it is a **Minervini / CAN SLIM-style momentum-breakout screener** (trend alignment + relative strength + proximity to 52-week highs + volume confirmation) wrapped in a Nifty + India-VIX regime filter.

---

## 2. The Daily Architecture

The system runs on a time-of-day schedule via Windows Task Scheduler. Each stage hands off to the next through JSON files.

| Time (IST) | Script | Role | Uses AI? |
|---|---|---|---|
| 8:55 / 9:00 AM | `morning_scan.py` → `morning_crew.py` | Scan 42 stocks, fetch news, ONE Claude call to approve ≤3 trades, size them, write `pending_orders.json` | Yes — news veto only |
| 9:16 AM | `execution_engine.py` | Fetch real Angel One opening prices, reject gap-ups >1.5%, recalc SL/target, log to `paper_trades.json` | No |
| 11:00 AM | `midday_sentinel.py` | Keyword-scan news + VIX + Nifty intraday regime for open positions; raise KILL/CAUTION flags | No |
| 9:30 AM – 3:15 PM (every 30 min) | `position_monitor.py` | Check SL / target / time-stop, close positions, update performance, trip circuit breakers | No |
| Sundays 18:00 (planned) | Weekly Tuner | Review week, adjust parameters | Yes (planned) |

**Design principle that is correct and worth preserving:** the LLM (Claude) never generates signals, never sizes trades, never does arithmetic. It receives a pre-scored, pre-filtered list and acts purely as a **veto layer** that reads unstructured news headlines and applies kill-rules. All scoring, sizing, instrument selection, and exits are deterministic Python. This is the right place — and the only right place — for an LLM in a trading loop.

---

## 3. The Signal Engine (the actual alpha logic)

Implemented in `morning_scan.py`. The score is built out of 14 points across six buckets.

| Bucket | Max pts | What earns the points |
|---|---|---|
| **Trend Alignment** | 3 | Price > MA20 > MA50 (2 pts); both MA20 and MA50 rising (1 pt) |
| **Relative Strength & 52W** | 4 | RS-rank percentile vs watchlist (0–2 pts); within 25% of 52-week high (1 pt); ≥30% above 52-week low (1 pt) |
| **Oscillator** | 1 | RSI between 40 and 65 |
| **Volume / Demand** | 3 | Volume ratio ≥ 1.0× (1 pt); OBV rising over 10 days (1 pt); breakout-day volume ≥ 1.5× average (1 pt) |
| **Volatility / Tradability** | 1 | Average daily range between 1.5% and 5.5% |
| **Market Regime Gate** | 2 | Nifty > 50-day MA (1 pt); Nifty > 200-day MA and 200-day MA rising (1 pt) |

**Dynamic threshold** (regime-aware): minimum score to trade is raised to **11/14 in a 0/2 regime**, **10/14 in a 1/2 regime**, and **9/14 in a 2/2 (healthy) regime**. A `trend_score >= 1` gate prevents RS-rank alone from promoting a LONG signal in a confirmed downtrend.

**Three strategy paths:**
- **MOMENTUM (long)** — the primary path; fires when the weighted score clears the dynamic threshold.
- **MEAN_REVERSION (long)** — Bollinger-band oversold + RSI < 35 + still above MA50.
- **SHORT** — price below both MAs + MA20 falling + MACD bearish.

**Exit framework:** ATR-based. Target = entry + 3×ATR, stop = entry − 2×ATR, with a 12-day time stop on equity and 7-day on options.

---

## 4. Risk & Capital Controls (implemented)

These are genuinely well thought out and are a strength of the system:

- **Position sizing:** risk-per-trade capped at the lower of ₹20,000 or 4% of capital, sized off the ATR stop distance.
- **Gap-risk cap:** equity quantity additionally capped so a worst-case 20% overnight gap cannot lose more than 2% of capital.
- **Concurrency limits:** max 3 simultaneous positions; max 1 per sector (Exchange allowed 2); max 80% capital deployed.
- **VIX-based instrument selection:** a decision matrix maps score + VIX regime to the instrument (equity / ATM call / bull-call-spread / iron condor), and halts all new entries when VIX ≥ 24.
- **Circuit breakers:** trip on 3 consecutive losses (2-day pause), daily loss > ₹15,000, or weekly loss > ₹40,000.
- **Gap rejection at execution:** `execution_engine.py` refuses to chase a LONG that gapped up >1.5%, refuses entries on >5% gap-downs (possible bad news), and never trades blind if the Angel One connection fails.
- **Deployment gate:** real capital is blocked until ≥20 trades, ≥45% win rate, positive expectancy, and ≤15% drawdown.

---

## 5. Mapping to Academic Research (NSE/BSE)

### What you implemented that the research supports

- **Momentum / relative strength — strongly validated.** The Agarwalla–Jacob–Varma factor model (IIM Ahmedabad) finds momentum is among the strongest factors in Indian equities — stronger than in the US — while **size is essentially worthless in India**. Your RS-rank and 52-week-high logic is the most research-justified part of the system.
- **Market-regime gating — validated.** The India VIX literature establishes VIX as a priced cross-sectional risk factor; your Nifty-MA regime gate plus VIX throttling is directionally sound.
- **Transaction-cost skepticism — the warning you have NOT yet heeded.** Mitra (*Quantitative Finance*, 2011) showed Indian moving-average/breakout rules beat random *gross* but lose most net alpha to transaction costs. Your system currently models no transaction costs (addressed in Document 2).

### What the research says you are missing (orthogonal alpha)

| Research edge | Source cluster | In code? | Value |
|---|---|---|---|
| Quality factor (QMJ) — ROE, low accruals, low leverage | Jacob–Pradeep–Varma, IIMA 2022 | No | **Highest-value add** |
| Post-earnings announcement drift (PEAD) | Multiple India studies | No (you *avoid* earnings) | **High, perfect fit for 5–12 day holds** |
| Per-stock implied volatility / skew | Jain–Varma–Agarwalla, *JFM* 2019 | No (single VIX gate only) | **High, for the options side** |
| Betting-against-beta / low-vol | Agarwalla et al., IIMA 2014 | No | Medium |
| Overnight volatility risk premium | Bhat et al., *JFM* 2024 | No | Medium (options only) |
| Asymmetric impact cost (buy > sell on NSE) | Tayal–Thomas, IGIDR | No | Medium (backtest honesty) |

**The pattern:** you've built the price-momentum + regime quadrant well, and have zero coverage of the quality, event-driven, and per-stock-volatility quadrants — which is where much of the *uncorrelated* Indian alpha lives.

---

## 6. Files & Data Inventory

**Core scripts:** `morning_scan.py` (scanner), `morning_crew.py` (orchestration + Claude veto + risk sizing), `execution_engine.py` (Angel One fills), `midday_sentinel.py` (news/VIX guard), `position_monitor.py` (exits + performance), `paper_trades.py` (manual journal utility), `watchlist_reader.py` (single-source-of-truth loader).

**State files:** `watchlist.json`, `strategy_memory.json` (the living config all scripts read), `paper_trades.json`, `decision_log.json`, dated `pending_orders_*.json` archives.

**Broker:** Angel One SmartAPI — currently used for LTP price fetching only; live order placement is planned, not built.

---

## 7. Current Operational Reality (from `decision_log.json`)

This is the single most important fact about the system today:

> In **6 trading days**, `morning_crew` ran **14 times**. It produced **exactly 3 orders**, all on one day (May 22). **Zero positions have ever closed.** Live performance reads `total_trades: 3, closed_trades: 0`.

The system is firing far too rarely to validate anything, and has not yet produced a single closed-trade data point. The reasons for this — and the fixes — are the entire subject of Document 2.

---

## 9. Changes Applied — 2026-06-02

### 9.1 Code Bugs Fixed

**`morning_crew.py` — `NameError: 'sym' is not defined` (was crashing daily)**

Root cause: inside `strict_risk_manager()`, the variable `sym` was used on two lines (sector map lookup and scan-file fallback) but was never assigned. The bug only surfaced when Claude returned trades missing `atr_sl`/`atr_target`, triggering the fallback block.

Fix — one line added after line 414:
```python
sym = trade.get("symbol")
```
The bug was latent from day one and masked because most approved trades already had ATR levels populated. It became fatal on 2026-06-02 when two trades were approved with missing levels.

---

**`midday_sentinel.py` — `NameError` on three undefined functions (crashing every midday run)**

Root cause: `main()` called three functions that were never implemented in the file — `scan_headlines()`, `check_market_wide_news()`, and `check_nifty_regime_intraday()`. The word-list constants they depend on (`KILL_WORDS`, `CAUTION_WORDS`, `MARKET_KILL_WORDS`) were correctly defined at module level (lines 66, 83, 91), but the functions themselves were simply never written.

Fix — three functions added immediately before `main()`:
- `scan_headlines(headlines, symbol)` — iterates headlines against `KILL_WORDS` and `CAUTION_WORDS`, returns a structured result dict.
- `check_market_wide_news()` — DuckDuckGo search for market-wide crisis keywords against `MARKET_KILL_WORDS`.
- `check_nifty_regime_intraday()` — fetches 60 days of Nifty data via yfinance, checks price vs 50-day MA, returns breach flag and levels.

This means `midday_sentinel.py` has **never successfully completed a run** since the system was built. The news/VIX guard for open positions was entirely non-functional.

---

### 9.2 Task Scheduler Hardening

**Root cause of all task failures:** every trading task was configured as **"Interactive only"** under user `tansh`, which is a Windows PIN account. This means tasks silently refuse to start whenever the screen is locked or the session is not active — the most common state during market hours on a laptop.

**All six tasks switched to `Run As User: SYSTEM`:**

| Task | Script | Previous State | New State |
|---|---|---|---|
| `TradingBot_MorningScan` | `morning_scan.py` | Interactive only / tansh | ✅ SYSTEM |
| `TradingBot_MorningCrew` | `morning_crew.py` | Interactive only / tansh | ✅ SYSTEM |
| `TradingBot_ExecutionEngine` | `execution_engine.py` | Interactive only / tansh | ✅ SYSTEM |
| `TradingBot_MidSentinel` | `midday_sentinel.py` | Interactive only / tansh | ✅ SYSTEM |
| `TradingBot_PositionMonitor` | `position_monitor.py` | Interactive only / tansh | ✅ SYSTEM |
| `TradingBot_WeeklyTuner` | `weekly_tuner.py` | Interactive only / tansh | ✅ SYSTEM |

SYSTEM always has background execution rights and is unaffected by PIN login, screen lock, or session state. All script paths and `Start In` directories (`C:\trading_bot`) are unchanged and accessible by SYSTEM.

**Implication for the operational reality in Section 7:** it is likely that many of the "14 runs, 3 orders, 0 closed trades" observation was further compounded by tasks silently not running on locked-screen days. The true run count may be lower than the log suggests.

---

### 9.3 Current Task Health (post-fix, verified 2026-06-02)

| Task | Last Result | Notes |
|---|---|---|
| `TradingBot_MorningScan` | 0 | Clean |
| `TradingBot_MorningCrew` | 1 | Today's run, pre-fix — will clear tomorrow |
| `TradingBot_ExecutionEngine` | 0 | Clean |
| `TradingBot_MidSentinel` | 1 | Today's run, pre-fix — will clear tomorrow |
| `TradingBot_PositionMonitor` | 0 | Clean |
| `TradingBot_WeeklyTuner` | -2147020576 | Historical (29-May, pre-fix) — will clear next Sunday |

---

## 10. Honest One-Line Verdict

The **strategy logic is sound** — it is a competent momentum-breakout system fishing in the one factor (momentum) the research says works best in India. But strategy is *not* what determines whether this system makes money. The measurement layer, cost realism, execution reliability, and sample size are — and those are the gaps documented in `02_Required_Fixes_and_Gemini_Prompts.md`.
