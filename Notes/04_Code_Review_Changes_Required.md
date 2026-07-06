# Code Review — Changes Required for Trading

**Document 4** | Step 2 of the build plan | Reviewed 2026-06-10 against ground-truth read of: `morning_scan.py`, `morning_crew.py`, `execution_engine.py` (targeted), `position_monitor.py` (targeted), `cost_model.py`, `strategy_memory.json`, `watchlist.json`, `paper_trades.json`, all `logs/*_autorun.log`, backtest engines (headers/structure).

Priority: **P0 = system is currently broken** · P1 = correctness before trusting any number · P2 = live-trading readiness.

---

## P0 — THE PIPELINE IS DEAD RIGHT NOW (since 2026-06-02)

The 2026-06-02 switch of all six Task Scheduler jobs to `Run As: SYSTEM` fixed the lock-screen problem and silently broke every job. The autorun logs show every scheduled run since then has crashed:

| Task | Crash | Evidence (last log lines) |
|---|---|---|
| `position_monitor.py` | `ModuleNotFoundError: pyotp` | logs/monitor_autorun.log — every 30-min run |
| `execution_engine.py` | `ModuleNotFoundError: pyotp` | logs/execution_autorun.log |
| `midday_sentinel.py` | `ModuleNotFoundError: yfinance` | logs/sentinel_autorun.log |
| `weekly_tuner.py` | `ModuleNotFoundError: anthropic` | logs/tuner_autorun.log |
| `morning_scan.py` (scheduled task) | `UnicodeEncodeError` (cp1252) on the `═` banner | logs/scan_autorun.log |
| `morning_crew.py` | `UnicodeEncodeError` (cp1252) on the `→` in the gap-cap print — crashes mid-Step-4 **only on days the gap cap triggers**, before orders are written | logs/crew_autorun.log |

**Two root causes:**
1. Your Python packages were installed for user `tansh` (pip --user site-packages). SYSTEM runs the same `C:\Python314` but does not see tansh's user site-packages → imports fail.
2. SYSTEM console defaults to cp1252; the scripts print Unicode (═, →, 📊, ⚠️). `morning_crew` sets `PYTHONIOENCODING=utf-8` for its *child* scan process (which is why `scan_2026-06-04/08.json` exist) but not for itself.

**Direct consequence:** the 3 paper positions opened 2026-05-22 (HINDALCO, BAJAJ-AUTO, ADANIPORTS) are still OPEN at 19 days — past the 12-day time stop — because the exit engine hasn't completed a run since. With real capital this would have been the unbounded-loss scenario Document 2 Gap 2 described.

### P0 fixes (do these before anything else)

**P0.1 — Dedicated venv + real requirements.txt.**
`requirements.txt` currently contains pasted notes, not requirements. Create a clean venv and freeze:
```cmd
cd C:\trading_bot
C:\Python314\python.exe -m venv venv
venv\Scripts\pip install yfinance ta pandas numpy anthropic python-dotenv pyotp logzero smartapi-python ddgs websocket-client
venv\Scripts\pip freeze > requirements.txt
```
Then edit all six Task Scheduler actions: Program = `C:\trading_bot\venv\Scripts\python.exe` (absolute path — SYSTEM has no PATH guarantees). Also change `morning_crew.py` line 89 `["python", ...]` → `[sys.executable, ...]` so the child scan uses the same interpreter.

**P0.2 — Encoding hardening.** Add to the top of every entry-point script (before any print):
```python
import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```
(or set system env vars `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` machine-wide). A crashed *print* must never again kill an order pipeline. Longer-term: route all output through `logging` with UTF-8 FileHandler; keep prints ASCII-only.

**P0.3 — Heartbeat + watchdog (Gap 2 can no longer be deferred — this outage is the proof).**
- `record_heartbeat(script, status)` appended to `heartbeat.json` at start AND clean-finish of each script (start/finish pair detects mid-run crashes, fixing the known false-alarm flaw of end-only heartbeats).
- `watchdog.py` scheduled 4×/day: checks expected runs for today, sends ONE Telegram message on missing/failed runs (python-telegram-bot or a 6-line `requests.post` to the Bot API; token in `.env`).

**P0.4 — Clean up the stuck trades.** After P0.1/P0.2, run `position_monitor.py` manually once: the 3 positions will close via time-stop. Mark them in `paper_trades.json` with `"data_quality": "OUTAGE_AFFECTED"` and exclude them from expectancy stats — their exits are 7 days later than the system would have done; the P&L is not representative.

**P0.5 — Run-as test.** `psexec -s -i cmd` (or a one-off SYSTEM task) that runs each script once and checks exit code 0 — prove the env works as SYSTEM before re-enabling the schedule.

---

## P1 — Correctness bugs and unfinished approved changes

**P1.1 — `vol_regime` is computed from the wrong variable (real bug, currently masked).**
`morning_crew.strict_risk_manager()`:
```python
avg_atr_pct = ...   # typical value 1.5–5.5 (stock ATR %)
if avg_atr_pct < vix_thresholds["low_vol_ceiling"]:   # 13.0 — an INDIA VIX level
    vol_regime = "low_vix"
```
Stock ATR% is compared against India-VIX thresholds (13/18/24) → `vol_regime` is **always `"low_vix"`**. Today it's harmless (decision matrix is equity-only) but every order record carries a wrong `vol_regime`, and the moment options are re-enabled, every score-11+ signal would route to `ATM_CALL` regardless of actual VIX. Fix: `vol_regime` from `live_vix` (already fetched 20 lines above): `<13 low / 13–18 normal / >18 high`; ATR% is not a VIX proxy.

**P1.2 — Position-count limit is not enforced.** Code comment says "No hard position limit — capital availability is the only constraint"; `max_simultaneous_positions` is loaded but never used; memory has it set to **20** while every document says **3**. With 80%-capital as the only cap, a high-signal day could open many small positions (sector limits are per-sector, not global). Decide the number (3 per docs), enforce it in `strict_risk_manager`, and align `strategy_memory.json`.

**P1.3 — Change A (5% monthly target) was approved but never applied.** `strategy_memory.json` still has `month_target_inr: 100000`, `month_target_pct: 20.0`, and `meta.deployment_criteria.target_monthly_return_pct: 20.0`. Apply the documented change (₹25,000 / 5.0 in all three places).

**P1.4 — Liquidity gate (Change B item 3) missing while small-caps are live.** `watchlist.json` now holds 55 names incl. 8 MIDCAP + 5 SMALLCAP, but `morning_scan.py` has no minimum-turnover gate. Add: skip if 20-day average traded value (`close × volume`) < ₹5 crore (constant `MIN_AVG_TRADED_VALUE_INR = 5e7`).

**P1.5 — Shadow logging (Gap 5) still not implemented.** No `shadow_signals.json` / `shadow_monitor.py` exist. This remains the single highest-value addition for sample size: log every score ≥ 7 signal with theoretical SL/target/time-stop; close them daily with `cost_model` applied; report expectancy by score bucket. (Prompt already written in Document 2.)

**P1.6 — Double data download in `morning_scan.py`.** `compute_rs_ranks()` downloads 13 months for all 55 symbols; `analyse_stock()` downloads the same data again per symbol → ~110 yfinance calls per run, slow and rate-limit-prone. Fetch once into a dict of DataFrames, pass to both. Add an on-disk parquet cache (refresh only today's bar) — this also becomes the data layer for the app and backtests.

**P1.7 — `rs_vs_market` (Gap 7 mitigation) not implemented.** Add benchmark-relative return field (vs `^CRSLDX`, fallback `^NSEI`) alongside the within-watchlist percentile, output-only.

**P1.8 — Stale `active_watchlist` in memory file.** `strategy_memory.json` says 42 names; `watchlist.json` has 55. `morning_crew` refreshes in-RAM but never persists; anything reading the JSON directly (e.g., your app, tuner) sees the stale list. Persist after refresh, or better: make `watchlist.json` the only reader path everywhere and delete the copy in memory.

**P1.9 — Deterministic earnings check missing.** The "skip if earnings within N days" rule is delegated to Claude reading headlines. Make it deterministic: fetch next-earnings date (yfinance `.calendar` as approximation; NSE corporate-actions feed later), compute `earnings_in_days`, pass it as a field, and hard-skip in `strict_risk_manager` — Claude keeps only the judgment cases.

**P1.10 — Smaller items.**
- `atm_strike = round(price/20)*20` — wrong strike step for most underlyings; when options return, read strike intervals from the instrument master.
- Option cost placeholders (premium = 2.5% of spot, spread = 0.9%, IC credit = 0.6%) are fiction — replace with Angel One option-chain quotes when options return (Gap 4's permanent fix).
- SHORT path's `score = short_score_raw + 2` pretends to be on the 14-pt scale — fine while shorts are SKIP'd; rework as a parallel 14-pt short score (CLAUDE.md architecture) before enabling.
- Claude prompt's example output says `"score": 7` / "Score 7/9" — stale V8 text inside a V9 prompt; tidy to avoid anchoring the model.
- `write_decision_log()` skipped-reason lookup can never find a reason (it searches `approved` for symbols already filtered out of `approved`) — every skip logs the default string. Ask Claude to return a `rejected` array with reasons, and log that.
- `news_data` ddgs client `DDGS()` instantiated once then re-instantiated per call inside `ddg_search` — harmless; delete the unused outer instance.
- 110 DDG queries/day for 55 symbols when only ~signals' news reaches Claude — restrict Step 2 to signal symbols + open positions (faster, less ban risk); sentinel re-queries independently anyway.

---

## P2 — Live-trading readiness (the build-out, in order)

**P2.1 — Unify the signal engine (one source of truth).** `morning_scan.py`, `backtest_v8.py`, `backtest_core.py` each re-implement scoring slightly differently (backtest_core is V3-era: fixed % stops, 10 stocks). Per the same-code-for-backtest-and-live principle: extract `engine/scoring.py` (pure functions: indicators → bucket scores → total) imported by the scanner, the backtests, and the app. Add the **mandatory no-lookahead unit test** (score at bar t unchanged when bars t+1… are appended) per CLAUDE.md §10.

**P2.2 — Order/position FSM + SQLite state.** Replace JSON-file position state with a SQLite tracker (`orders` table: state ∈ PENDING→PLACED→PARTIAL→FILLED→CLOSED/REJECTED/ERROR, with complete transition matrix and defined gap-recovery per playbook B3.1). JSON stays as a human-readable export, not the source of truth.

**P2.3 — Execution upgrade path (paper → live).** Current `execution_engine.py` is solid for paper (gap rejection, slippage tiers, abort-on-API-failure, orphan detection). For live add, in this order: marketable-limit (IOC) orders with price caps → order-status polling loop with timeout-cancel (never be short the latency option) → partial-fill handling → broker-position reconciliation at start/end of day → `state/kill_switch.json` honored by every script, never auto-reset.

**P2.4 — Risk layer extraction.** `risk/manager.py` as the single deterministic gatekeeper (`risk.check(order)`) used by crew and (later) live execution; exhaustive regime × score × VIX matrix unit test per CLAUDE.md; plus a `panic_flatten.py` (close everything now, paper or live).

**P2.5 — FFC for the weekly tuner.** Give `weekly_tuner.py` a deterministic core before any LLM commentary: per-sleeve rolling fitness (RTNAV over last 10–20 closed/shadow trades); fitness < 0 ⇒ sleeve risk-off (shadow continues), fitness recovers ⇒ risk-on. The LLM may explain; only the formula decides.

**P2.6 — Python version risk.** You're on 3.14 (it already killed CrewAI). The venv pin (P0.1) freezes today's wheels; when any dependency breaks, the escape hatch is a 3.11/3.12 venv — nothing in the codebase requires 3.14.

**P2.7 — Options re-enable preconditions** (gate, do not rush): real option-chain pricing + per-stock IV rank feed → honest options P&L in monitor (kill the ×1.0/−0.5/−0.3 hardcodes) → strike-interval + lot-size verification against instrument master → expiry-week force-exit for stock options (physical delivery) → only then flip the decision matrix back per the playbook C3/C6 matrix.

---

## What is already GOOD (verified — keep, don't churn)
- Gap 1 fix is correctly in place: `gross_win_sum_inr`/`gross_loss_sum_inr` persisted and used for averages; best/worst kept separate.
- `cost_model.py`: clean named constants, verify-flags, both segments.
- `execution_engine.py`: gap rejection (>1.5% chase / >5% gap-down), slippage tiers by liquidity, abort-never-blind on Angel One failure, orphan-order detection.
- Architecture discipline: Claude = veto-only, one call/day, strict JSON, deterministic sizing — exactly the right shape (the books independently confirm: price-driven beats news-driven; LLM belongs at the language boundary only).
- `watchlist_reader.py` single-source-of-truth pattern.

## Carry-forward checklist (ordered)
1. P0.1 venv + scheduler paths → P0.2 encoding → P0.5 SYSTEM smoke test
2. P0.3 heartbeat/watchdog/Telegram → P0.4 close stuck trades, tag data
3. P1.1 vol_regime fix · P1.2 position cap · P1.3 5% target · P1.4 liquidity gate
4. P1.5 shadow logging · P1.6 single-fetch + cache · P1.7 rs_vs_market · P1.8 watchlist persist · P1.9 earnings field
5. P2.1 unified scoring engine + no-lookahead test (prereq for honest backtests in Step 3)
6. P2.2–P2.5 as the live-readiness track; P2.7 options gate last
