# CLAUDE.md — Standing Instructions for the Trading Bot Repo

**Owner:** Diptanshu Raj
**Markets:** NSE Cash + F&O (Indian equities only)
**Broker:** Angel One SmartAPI (primary), yfinance (fallback for market data only)
**Status:** Paper trading. **Zero real capital deployed.** Do not change this without an explicit instruction in chat that says "go live".
**System version:** V9 (per UpdatedLogic21stMay.pdf, 21 May 2026)

---

## 1. What this repo is

A long-only Indian-equity trading bot with optional ATM call / call-spread overlay. Daily-bar horizon (5–20 day holds). Universe is the 55-stock NSE watchlist defined in `config/watchlist.json`. The architecture has five blocks in strict left-to-right order:

```
Brain (engine/)  →  Allocation (allocation/)  →  Safety (risk/)  →  Broker (broker/)  →  Dashboard (dashboard/)
```

The Brain proposes. Allocation sizes. Safety vetoes. Broker executes. Dashboard observes. **The Brain never talks to the Broker directly.**

---

## 2. Hard rules — never violate

1. **Never invent a SmartAPI method.** If unsure of the current SDK signature, write a TODO with the exact line of the Angel One SmartAPI documentation you need verified. Do not guess parameter names.

2. **Never write code that bypasses `risk/manager.py`.** All order calls go through `risk.check()` first. The Brain cannot call `broker.place()` directly. Ever.

3. **No lookahead.** If a computation uses bar `t` to make a decision for bar `t`, flag it and ask. Default rule: decisions for bar `t` use only bars `≤ t-1`, except when the decision is "place a market order at next open" — which is fine because the open is the actual fill, not the close that informed the decision.

4. **No API keys, TOTP secrets, client codes, or PINs in code, logs, commit messages, scan files, decision logs, or any committed artifact.** Read from `.env` via python-dotenv. `.env` is gitignored. If you see one in code, treat it as a critical bug and stop.

5. **Backtest changes always run walk-forward.** Single in-sample fits are never an acceptable result. `backtest/walkforward.py` is the entry point. If a result was not produced through that file, do not trust it.

6. **Every new module ships with a unit test in `tests/`.** Pull requests without tests are not done. Tests must run in under 30 seconds total for the engine/ + allocation/ + risk/ packages.

7. **No real-money trading without an explicit "go live" instruction from Diptanshu in chat.** The broker module's `live_enabled` flag defaults to False; flipping it requires (a) walk-forward backtest positive after Indian costs, (b) 2 weeks of paper trading with results matching backtest, (c) all kill-switch / reconciliation tests passing.

8. **Never auto-resume after a kill.** If `state/kill_switch.json` shows killed=true, the orchestrator refuses to trade until a human edits the file. The Brain cannot un-kill itself.

---

## 3. The 5-component architecture — directory layout

```
trading_bot/
├─ CLAUDE.md
├─ pyproject.toml
├─ .env.example
├─ config/
│  ├─ strategy.yaml               # thresholds, multipliers, hyperparameters
│  ├─ watchlist.json              # single source of truth, 55 stocks
│  └─ strategy_memory.json        # sector limits, decision matrix
├─ data/                          # cached parquet OHLCV (gitignored)
├─ artifacts/                     # fitted HMM .pkl (gitignored)
├─ engine/                        # BRAIN
│  ├─ regime.py                   # rule-based 5-state classifier
│  ├─ hmm_regime.py               # HMM 5-state classifier (with label-switch fix)
│  ├─ regime_ensemble.py          # combines rule + HMM
│  ├─ features.py                 # RSI, OBV, ATR, MA, slope, vol — all here
│  ├─ scoring.py                  # 14-point long score (6 buckets)
│  ├─ short_scoring.py            # parallel 14-point short score
│  ├─ mean_reversion.py           # parallel sleeve (BB + RSI<35 + above 50dma)
│  └─ decision.py                 # regime × score → Action
├─ allocation/                    # ALLOCATION
│  ├─ base.py                     # vol-targeted base position size
│  ├─ regime_multiplier.py        # regime → size multiplier
│  ├─ stops.py                    # ATR-based stops
│  └─ sector_caps.py              # reads strategy_memory.json
├─ risk/                          # SAFETY (independent of AI)
│  ├─ manager.py                  # the gatekeeper; every order passes through
│  ├─ kill_switch.py              # persisted flag (state/kill_switch.json)
│  ├─ vix_tiers.py                # Normal/Elevated/Dangerous/Extreme logic
│  └─ news_filter.py              # kill-word matcher (midday_sentinel uses this)
├─ broker/                        # BROKER
│  ├─ base.py                     # abstract Broker, Order dataclass
│  ├─ angelone.py                 # SmartAPI adapter
│  ├─ paper.py                    # paper broker (mirrors interface)
│  └─ options.py                  # ATM strike selection, option chain
├─ orchestrator/                  # the long-lived loop
│  ├─ morning_scan.py             # 09:15 IST: scan universe, write scan_YYYY-MM-DD.txt
│  ├─ morning_crew.py             # Claude API call for qualitative review of top scores
│  ├─ midday_sentinel.py          # 11:00–14:30 IST: VIX, news, intraday Nifty regime
│  └─ execution_engine.py         # places orders that pass risk.check()
├─ dashboard/                     # DASHBOARD
│  └─ streamlit_app.py
├─ daily_reports/                 # scan_YYYY-MM-DD.txt (read-only outputs)
├─ state/                         # kill_switch.json, positions.json (runtime state)
├─ logs/                          # rotating logs per module
└─ tests/                         # pytest suite, < 30s for unit tests
```

---

## 4. The 14-point scoring rubric (V9, immutable contract)

Stocks are scored 0–14 across 6 buckets. **Do not change point allocations without updating the weightage research paper and re-running the full walk-forward.**

| Bucket | Pts | Conditions |
|---|---|---|
| A. Trend Alignment | 3 | Price > MA50 AND MA20 > MA50 (2) + MA20 rising AND MA50 rising (1) |
| B. Relative Strength + 52W Position | 4 | 12-mo return top 25% (2) or top 50% (1) + within 25% of 52W high (1) + ≥30% above 52W low (1) |
| C. Oscillator | 1 | RSI(14) in [40, 65] |
| D. Volume / Demand | 3 | 20d avg vol > 1.0x baseline (1) + OBV 20d slope > 0 (1) + breakout candle vol > 1.5x (only if 20d high in last 5 sessions) (1) |
| E. Volatility / Tradability | 1 | ATR(14) / Price > 0.015 |
| F. Market Regime | 2 | Nifty > 50dma (1) + Nifty > 200dma AND 200dma rising (1) |

**Decision thresholds:**

| Score | Verdict | Instrument |
|---|---|---|
| 11–14 | STRONG BUY | ATM_CALL (low VIX) / Call spread (elevated VIX) |
| 9–10 | CONSIDER | EQUITY only, no leverage |
| < 9 | WAIT | No trade |

**Hard veto:** If Bucket F1 = 0 (Nifty below its 50dma), no long signal fires regardless of total score.

---

## 5. The 5-regime overlay

Layered on top of Bucket F. Read from `engine/regime_ensemble.py`. The full action matrix:

| Regime | Score 11–14 | Score 9–10 | Mean-rev fires |
|---|---|---|---|
| Crash | HARD VETO | HARD VETO | Disabled |
| Bear | EQUITY half size if score ≥ 12 | — | Half size |
| Neutral | EQUITY or ATM_CALL (VIX-dependent) | EQUITY half size | Full size |
| Bull | ATM_CALL / Call spread | EQUITY full size | Half size |
| Euphoria | Call spread only, cap exposure 50% | — | Disabled |

Regime is computed once per morning (and re-checked intraday by `midday_sentinel.py`). The decision matrix lives in `config/strategy_memory.json` so it can be edited without code change.

---

## 6. Mean reversion sleeve — parallel, not co-mingled

Triggers when **all three** conditions fire:

- Price is at or below lower Bollinger Band
- RSI(14) < 35
- Price > 50-day MA (so it's a dip in an uptrend, not a falling knife)

When fires, strategy label = MEAN_REVERSION, instrument = ATM_CALL, regardless of the 14-point score.

**Disabled in Crash and Euphoria regimes.**

The mean-rev sleeve has its own capital budget (20% of portfolio); it does not consume the trend sleeve's 80%.

---

## 7. Safety / Risk Engine — the bedrock principle

The image's diagram says explicitly: **Safety works independently of AI model.** Concretely:

- All risk parameters live in `config/strategy_memory.json` (sector limits) and `config/strategy.yaml` (daily loss cap, max position, etc.)
- The Claude API call in `morning_crew.py` cannot edit these files.
- `risk/manager.py.check(order)` is pure and deterministic.
- The kill switch persists to `state/kill_switch.json`. A process restart reads the flag at startup.

**VIX tiers (V9):**

| Tier | India VIX | Action |
|---|---|---|
| Normal | < 18 | No restriction |
| Elevated | 18–22 | CAUTION flag; no new ATM_CALL trades |
| Dangerous | 22–24 | KILL flag; close all at 15:30 IST |
| Extreme | ≥ 24 | KILL flag; close immediately |

**Kill words in news (V9):** earnings miss, below estimate, below expectations, weak guidance, guidance cut, guidance withdrawn, fpi selling, margin call, credit watch.

**VIX-fetch failure mode:** If both Angel One and yfinance fail to return India VIX, return None and log; treat as Caution, NOT as Kill. (V9 fixed the previous bug where fetch failure caused false kills.)

---

## 8. Watchlist — single source of truth

All scripts read from `config/watchlist.json` via `watchlist_reader.py`. **Never hardcode symbols in scripts.** To add, remove, or pause a stock, edit only `watchlist.json`.

To pause without deleting:

```json
{ "symbol": "VEDL", "active": false }
```

55 stocks across these sectors (with limits in `strategy_memory.json`): IT, Banking, Energy, Metals, Defence, Defence Electronics, Power, Telecom, Exchange.

Stocks with `lot_size = 1` (non-F&O): NETWEB, DSSL, DATAPATTNS, HFCL, DCXINDIA, GRAVITA — route to EQUITY only.

---

## 9. Coding conventions

- Python 3.11, type hints required on public functions.
- `ruff` and `black` formatting (config in `pyproject.toml`).
- Pure functions where feasible. Side effects live in `broker/`, `orchestrator/`, and `risk/kill_switch.py`.
- No use of `print()` outside CLI entry points. Use the module logger.
- Logs go to `logs/<module>.log` with rotation. Never log API keys, TOTP, PINs, or session tokens.
- All datetimes are timezone-aware; default `Asia/Kolkata` (IST).
- Money quantities are `float` for now; if precision becomes an issue, switch to `Decimal` and update strategy.yaml.

---

## 10. Test policy

Every new module under `engine/`, `allocation/`, `risk/`, `broker/`, `orchestrator/` ships with:

- A unit test (`tests/test_<module>.py`)
- For `engine/scoring.py`: **a mandatory no-lookahead test** that asserts the score for day `t` does not change when future bars (`t+1`, `t+2`, …) are appended to the input
- For `risk/manager.py`: an exhaustive matrix coverage test of regime × score × VIX-tier
- For `broker/angelone.py`: tests use a mocked SmartConnect; live network calls are forbidden in tests
- For `broker/paper.py`: the paper broker must be a perfect drop-in replacement for `angelone.py` (same interface)

Run all tests before any commit: `pytest -x tests/`. The suite must finish in under 30 seconds for the unit-test portion.

---

## 11. The Claude API call (`morning_crew.py`)

The orchestrator calls Claude after `morning_scan.py` produces its top candidates. The contract is:

- **Inputs:** top 5 scored stocks (score, breakdown by bucket, regime, last 30 daily bars summarised, last 5 headlines)
- **Output:** strict JSON matching this schema (no other format accepted):

```json
{
  "decisions": [
    {
      "symbol": "RELIANCE",
      "go_long": true,
      "confidence": 0.78,
      "reasoning_short": "≤ 40 words"
    }
  ]
}
```

- **Hard rules in the prompt:**
  - If `regime == "CRASH"`, every `go_long` must be false regardless of confidence
  - If `confidence < 0.6`, `go_long` must be false
  - The Claude call cannot bypass the 14-point score — it can only veto a high-scoring trade, never up-vote a low-scoring one

---

## 12. Things to remind me about before doing them

- Switching `broker.live_enabled = True` (real money goes live)
- Editing `strategy.yaml` `daily_loss_cap_pct` upwards (loosens safety)
- Editing `strategy_memory.json` sector limits (loosens diversification)
- Adding any stock with `lot_size = 1` to an options route
- Removing the F1 hard veto (Nifty < 50dma)
- Anything that touches `state/kill_switch.json`

For each: stop, summarise what's about to change, and wait for an explicit confirmation in chat.

---

## 13. Source of truth for decisions

When in doubt about *why* the system was built this way:

1. **The weightage research paper** (`Notes/ResearchPaper on Weightage distribution.pdf`) — explains the 14-point distribution with academic citations.
2. **The V9 change log** (`Notes/UpdatedLogic21stMay.pdf`) — explains what changed from V8 → V9 and why.
3. **The v2 report** (`Notes/Automate_Trading_using_Claude_v2.docx`) — explains the full 5-component architecture, the 5-regime overlay, and the integration with V9.

If a proposed change contradicts any of these documents, stop and surface the contradiction.

---

## 14. Final disclaimer

This system is paper-trading until further notice. None of the strategies, scoring rules, regime classifications, or instrument selections in this repo or accompanying documents constitute financial advice. SEBI rules on algorithmic trading evolve — re-verify before any go-live decision.

---

*CLAUDE.md last reviewed: 23 May 2026. Next review: when V10 ships, or when broker / SEBI rules change, whichever sooner.*
