# Gemini CLI Prompts — Executing Document 04

**Document 6** | Companion: `04_Code_Review_Changes_Required.md` (the *why* for every prompt) and `05_Backtest_Report.md` (evidence)
**Workflow (your established pattern):** run `gemini` from `C:\trading_bot` → paste ONE prompt → Gemini shows plan/diff → **you review → approve → it writes**. Never approve a diff you haven't read. One prompt at a time, in the order below.

Items marked **🔧 MANUAL** cannot be done by editing code — exact commands are given instead.

---

## PHASE 0 — Resurrect the pipeline (do today, in this order)

### 🔧 MANUAL 0.A — Create the venv (this is the actual fix for the dead pipeline)

```cmd
cd C:\trading_bot
C:\Python314\python.exe -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\pip install yfinance pandas numpy anthropic python-dotenv pyotp logzero smartapi-python ddgs websocket-client ta requests
venv\Scripts\pip freeze > requirements.txt
```
Note: `requirements.txt` currently contains pasted notes, not requirements — this overwrites it with a real one. If you want to keep the notes, move them to `Notes\` first.

### PROMPT 0.B — Point everything at the venv interpreter

```
Context: all six Windows Task Scheduler jobs run as SYSTEM and crash with ModuleNotFoundError because they call the global python instead of the new venv at C:\trading_bot\venv\Scripts\python.exe.

Tasks:
1. Search create_tasks.ps1, setup_tasks.bat, ts_query.ps1 and all task_*.xml files in this repo for any reference to "python", "python.exe", "pythonw" or a hardcoded interpreter path. Show me every occurrence with file and line.
2. Update each so the program/action is the absolute path C:\trading_bot\venv\Scripts\python.exe (keep all arguments and "Start in" directory C:\trading_bot unchanged).
3. In morning_crew.py, the run_technical_scan() function calls subprocess.run(["python", str(SCAN_SCRIPT)], ...). Change "python" to sys.executable (add "import sys" if missing) so the child scan always uses the same interpreter as the parent.
4. Do NOT register or run any scheduled task yourself — only edit the files. I will re-register manually.

Show me every change as a diff before writing anything.
```
**🔧 MANUAL after approval:** re-register the tasks (run your `setup_tasks.bat` / `create_tasks.ps1` as admin, or edit each task's Action in Task Scheduler GUI to the venv path), then verify:
```cmd
schtasks /Query /TN "TradingBot_PositionMonitor" /V /FO LIST | findstr "Task To Run"
```

### PROMPT 0.C — UTF-8 hardening (kills the cp1252 crashes)

```
Context: under the SYSTEM account the console encoding is cp1252, and Unicode characters in print statements (box-drawing ═, arrows →, emoji) crash the scripts with UnicodeEncodeError. morning_scan.py died at its banner; morning_crew.py died mid-Step-4 at the gap-cap print — before orders were written.

Tasks:
1. Add this exact block at the very top of each of these entry-point scripts, immediately after the module docstring and BEFORE any other import or print: morning_scan.py, morning_crew.py, execution_engine.py, position_monitor.py, midday_sentinel.py, weekly_tuner.py, paper_trades.py:

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

2. In morning_crew.py only, also replace the "→" character in the gap-risk-cap print inside strict_risk_manager() with "->" (ASCII), as defence in depth.
3. Change nothing else. No logic changes.

Show me the diff for every file before writing.
```

### PROMPT 0.D — Heartbeat + watchdog + Telegram alert (Gap 2, upgraded)

```
Context: the whole pipeline silently failed for 8 days because nothing monitors whether scheduled scripts actually ran. Build a heartbeat system. Important design requirement: record BOTH a START and a FINISH event per run, so a crash mid-run (start without finish) is detectable — a finish-only heartbeat gives false comfort.

1. Create C:\trading_bot\heartbeat.py with:
   - record_heartbeat(script_name: str, status: str) -> None
     Appends {"script": script_name, "status": status, "timestamp": ISO-8601 local time, "date": "YYYY-MM-DD"} to heartbeat.json (create as [] if missing). status is "START", "FINISH", or "ERROR". Keep only the last 60 days of entries on each write. The function must NEVER raise — wrap everything in try/except and fail silent.
   - check_today(expected: dict) -> list
     expected maps script_name -> "HH:MM" latest expected start time today. Returns a list of problem dicts: {"script", "problem"} where problem is "NO_START" (no START today after its expected time has passed), or "NO_FINISH" (START exists but no later FINISH/ERROR for that run).
2. Instrument these scripts: morning_scan.py, morning_crew.py, execution_engine.py, midday_sentinel.py, position_monitor.py, weekly_tuner.py.
   - record_heartbeat(<name>, "START") as the first statement of main() (or the __main__ block if no main()).
   - Wrap the body so that normal completion calls record_heartbeat(<name>, "FINISH") and any uncaught exception calls record_heartbeat(<name>, "ERROR") and then re-raises. Use try/except/else or try/finally — preserve existing exit behaviour and return values exactly.
3. Create C:\trading_bot\watchdog.py:
   - Expected schedule: morning_scan 09:00, morning_crew 09:10, execution_engine 09:20, midday_sentinel 11:05, position_monitor 15:20 (it runs every 30 min; only check that at least one START+FINISH pair exists after 09:30).
   - Calls heartbeat.check_today(); on any problem, builds one plain-text message listing all problems and sends it via Telegram Bot API using requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10). Read TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env via python-dotenv. If they are missing, print the message and write it to logs/watchdog_alerts.log instead of sending. Also append every alert to decision_log.json as {"date", "time", "type": "MISSED_RUN", "details": [...]}.
   - Only run checks Monday-Friday; exit quietly on weekends.
4. Apply UTF-8 stdout reconfigure at the top of watchdog.py like the other scripts.

Show me the plan and the full content of heartbeat.py and watchdog.py before writing, then the diffs for the six instrumented scripts.
```
**🔧 MANUAL after approval:** create the Telegram bot (@BotFather → token; message the bot once; get chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`), add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`, and add a scheduled task for `watchdog.py` at 09:30, 11:30, 15:45 using the venv python.

### PROMPT 0.E — Tag the outage-corrupted trades

```
In paper_trades.json there are exactly 3 trades, all dated 2026-05-22 (HINDALCO, BAJAJ-AUTO, ADANIPORTS), still status OPEN because position_monitor.py crashed on every run from 2026-06-02 to 2026-06-10 (the exit engine was down; they overshot the 12-day time stop).

1. Add the field "data_quality": "OUTAGE_AFFECTED" to each of these 3 trade objects in paper_trades.json. Touch nothing else in the file; preserve formatting/indent=2.
2. In position_monitor.py update_performance(), exclude trades where trade.get("data_quality") == "OUTAGE_AFFECTED" from ALL statistics (wins, losses, sums, averages, expectancy, streaks) — but still allow check_exit() to close them normally and write their pnl to the trade record. Add a one-line comment explaining why.

Show me both diffs before writing.
```
**🔧 MANUAL after approval:** run `venv\Scripts\python.exe position_monitor.py` once interactively — the 3 positions should close via time-stop; check `paper_trades.json` afterwards.

### 🔧 MANUAL 0.F — SYSTEM smoke test (prove it before re-enabling the schedule)

```cmd
schtasks /Run /TN "TradingBot_MorningScan"
timeout /t 60
type C:\trading_bot\logs\scan_autorun.log
schtasks /Run /TN "TradingBot_PositionMonitor"
timeout /t 60
type C:\trading_bot\logs\monitor_autorun.log
```
Both must end without a traceback. Only then leave the schedule armed.

---

## PHASE 1 — Correctness fixes (this week, after Phase 0 is green)

### PROMPT 1.1 — Fix the vol_regime bug (wrong variable compared)

```
Bug in morning_crew.py, strict_risk_manager(): vol_regime is derived from avg_atr_pct (average stock ATR%, typical range 1.5-5.5) compared against memory["instrument_selection"]["india_vix_thresholds"] values (low_vol_ceiling=13.0, high_vol_floor=18.0) — which are India VIX levels, not ATR percentages. Result: vol_regime is ALWAYS "low_vix". Today it is masked because the decision matrix is equity-only, but every order record carries a wrong vol_regime and the bug will misroute instruments the day options are re-enabled.

Fix:
1. The function already fetches live_vix from today's scan JSON a few lines above. Derive vol_regime from live_vix instead:
   - live_vix < low_vol_ceiling (13.0)  -> "low_vix"
   - low_vol_ceiling <= live_vix < high_vol_floor (18.0) -> "normal_vix"
   - live_vix >= high_vol_floor -> "high_vix"
   - live_vix is None/0 -> "normal_vix" and print a warning that VIX was unavailable (conservative default; never default to low_vix).
2. Delete the avg_atr_pct computation and its comparison block entirely (it has no other consumers — verify with a search and show me if you find any).
3. Keep the existing extreme-VIX halt and risk-halving logic above unchanged.

Show me the diff before writing.
```

### PROMPT 1.2 — Enforce the position-count limit

```
In morning_crew.py strict_risk_manager() there is a comment "# No hard position limit — capital availability is the only constraint" and the loaded max_positions value is never used. Documentation (Notes/01, CLAUDE.md) specifies max 3 simultaneous positions; strategy_memory.json currently says 20.

1. In strategy_memory.json set capital_rules.max_simultaneous_positions to 3.
2. In strict_risk_manager(), before evaluating each approved trade, compute open_count = current_open + len(final_orders) and skip (with a printed reason "position cap 3 reached") any trade that would exceed max_positions. Place this check FIRST in the loop, before sector checks.
3. Remove the misleading comment.

Show me both diffs before writing.
```

### PROMPT 1.3 — Apply the approved 5% monthly target (Change A — approved in Document 02 but never executed)

```
In strategy_memory.json, change the monthly profit target from 20% to 5%, consistently:
1. performance_live.month_target_pct: 20.0 -> 5.0
2. performance_live.month_target_inr: 100000 -> 25000 (5% of 500000 capital)
3. meta.deployment_criteria.target_monthly_return_pct: 20.0 -> 5.0
4. Update any note/string fields in the JSON that still reference 20% or 1,00,000 as the target.
5. Search all .py files for hardcoded 100000, "20%", or month_target references that would also need updating — report them to me but do NOT change Python files without showing me first.

Show me every change as a diff.
```

### PROMPT 1.4 — Liquidity gate (small/mid-caps are live without one)

```
watchlist.json now contains 55 names including 8 MIDCAP and 5 SMALLCAP, but morning_scan.py has no minimum-liquidity gate. Illiquid names produce backtest fills you could never get live.

In morning_scan.py:
1. Add a module-level constant MIN_AVG_TRADED_VALUE_INR = 50_000_000  # Rs 5 crore/day, configurable.
2. In analyse_stock(), compute avg_traded_value = (close * volume) 20-day rolling mean, latest value. Add "avg_traded_value_cr" (rounded, in crore) to the returned dict.
3. If avg_traded_value < MIN_AVG_TRADED_VALUE_INR: force strategy "WAIT", direction "—", atr_sl/atr_target None, and append "ILLIQUID (<5cr/day)" to conditions so the report shows why. Do this AFTER the score is computed (keep the score visible for information) but make it impossible for the stock to enter the signals list.
4. No other logic changes.

Show me the diff before writing.
```

### PROMPT 1.5 — F1 hard veto (spec says it, code doesn't; backtest says +₹100/trade)

```
CLAUDE.md section 4 specifies: "Hard veto: If Bucket F1 = 0 (Nifty below its 50dma), no long signal fires regardless of total score." morning_scan.py does NOT implement this — it only raises the minimum score to 11 when regime is 0/2. The 2019-2026 backtest (Notes/05_Backtest_Report.md section 1) shows regime-0 trades averaged -Rs80 and the hard veto improves profit factor 1.16 -> 1.26 and cuts max drawdown 15.2% -> 12.9%.

In morning_scan.py run_morning_scan():
1. After nifty_regime is fetched, if nifty_regime['f1'] is False: print a prominent line "F1 HARD VETO — Nifty below 50dma, no new LONG entries today (CLAUDE.md §4)".
2. In the per-stock loop, when f1 is False, force every LONG result to verdict "⏳ WAIT (F1 veto)", strategy "WAIT", direction "—", atr_sl/atr_target None — regardless of score. Keep computing and displaying the score itself. SHORT logic is untouched (it is SKIPped downstream anyway).
3. The saved JSON's "signals" array must be empty of LONGs on veto days.

Show me the diff before writing.
```

### PROMPT 1.6 — Single data fetch + on-disk cache (kills the double download)

```
morning_scan.py downloads 13 months of history TWICE for every stock: once in compute_rs_ranks() and again in analyse_stock() — ~110 yfinance calls per run, slow and rate-limit-prone.

Refactor, minimal-risk version:
1. Add fetch_all_history(watchlist) that downloads each symbol's 13-month daily history ONCE into a dict {symbol: DataFrame}, with a retry (2 attempts, 2s backoff) per symbol, and returns the dict. Use the same yf.Ticker(sym + '.NS').history(...) call as today so data is identical.
2. compute_rs_ranks(watchlist, data) and analyse_stock(symbol, df) take the pre-fetched data instead of downloading. Keep their outputs byte-identical for the same inputs.
3. Add a simple disk cache: after fetching, pickle the dict to data_cache/hist_YYYY-MM-DD.pkl; on startup, if today's file exists and is younger than 6 hours, load it instead of downloading (so crew's subprocess scan and a manual re-run don't re-download). Create the data_cache/ folder; add a comment that it is safe to delete.
4. Run order in run_morning_scan(): fetch once -> rs ranks -> per-stock analysis.

Do not change any indicator math, scoring, thresholds, or output formats. Show me the full plan first, then the diff.
```

### PROMPT 1.7 — Market-relative RS field (Gap 7 mitigation, output-only)

```
In morning_scan.py, compute_rs_ranks() ranks 12-month returns only WITHIN the watchlist (self-referential; survivorship-biased). Add a market-relative benchmark, output-only:

1. Fetch 13 months of ^CRSLDX (Nifty 500); if it fails or has <200 bars, fall back to ^NSEI. Compute its 12-month return.
2. For each stock add "rs_vs_market" = stock_12m_return - benchmark_12m_return (percentage points, rounded 1dp) to the analysis dict and to the saved JSON.
3. Also add "ret_12m_pct" (the raw 12-month return) per stock.
4. Do NOT change the existing rank or any scoring weight — these fields are for the shadow system to evaluate which is more predictive.

Show me the diff before writing.
```

### PROMPT 1.8 — Shadow logging (Gap 5 — the sample-size multiplier, upgraded with backtest columns)

```
Build a shadow-signal system that records EVERY signal for validation, fully isolated from paper capital. Design requirements:

1. morning_scan.py: after the scan completes, append to shadow_signals.json one entry per stock with score >= 7 OR mr_signal true, fields: date, symbol, score, strategy, direction ("LONG" only for now), price, atr, atr_sl, atr_target, rs_rank, rs_vs_market, ret_12m_pct, regime_pts, f1, status "SHADOW_OPEN", days_held 0. Never let shadow logging raise — wrap in try/except; it must not interfere with the normal scan output.
2. Create shadow_monitor.py (model it on position_monitor.py's equity exit math):
   - For every SHADOW_OPEN entry, fetch the current price (yfinance), then close at theoretical levels: SL touched (use day Low <= atr_sl -> exit at atr_sl; if day Open < atr_sl exit at Open), target touched (day High >= atr_target -> exit at atr_target; if Open > target exit at Open), or 12-trading-day time stop at Close. Check SL before target on the same day (pessimistic).
   - On close: compute pnl_gross_inr for a NOTIONAL qty = 20000 / (2*atr) rounded down (same sizing as live), then subtract cost_model.transaction_cost for both sides into pnl_net_inr and costs_inr. Set status "SHADOW_CLOSED", exit_date, exit_reason.
   - LONG-only for now; structure the P&L line so a SHORT branch can be added later (sign flip) — add a TODO comment.
   - shadow_monitor must NEVER touch paper_trades.json or strategy_memory.json.
3. Add shadow_performance() in shadow_monitor.py: prints and returns win rate, expectancy (net), profit factor, avg days held, count — broken down by score bucket (7-8, 9-10, 11-14), by strategy, by regime_pts at entry, and by rs_rank quartile. This is what decides future re-weighting, so make the table easy to read.
4. Add heartbeat START/FINISH instrumentation and the UTF-8 stdout reconfigure like the other scripts.

Show me the plan and file structure first; after I approve, show full diffs.
```
**🔧 MANUAL after approval:** add a Task Scheduler job for `shadow_monitor.py` daily 15:45, venv python.

### PROMPT 1.9 — Persist the watchlist refresh + deterministic earnings field

```
Two small correctness items in morning_crew.py:

1. main() refreshes memory["stock_universe"] (active_watchlist, sector_map, lot_sizes) from watchlist.json in RAM but never saves, so strategy_memory.json on disk is stale (says 42 stocks; watchlist has 55). After the refresh, write strategy_memory.json back to disk (indent=2) so every consumer sees the same universe. Guard with try/except so a write failure cannot stop the run.
2. The "skip if earnings within N days" rule currently relies on Claude reading headlines. Make it deterministic:
   - Add get_earnings_in_days(symbol) using yfinance: ticker.calendar / get_earnings_dates() (handle both dict and DataFrame returns; any exception -> None). Comment that yfinance earnings dates are approximate and the NSE corporate-actions feed is the better source later.
   - For each signal passed to Claude, add field "earnings_in_days".
   - In strict_risk_manager(), HARD-SKIP any trade with earnings_in_days is not None and 0 <= earnings_in_days <= memory["entry_rules"]["news_sentiment_override"]["earnings_within_days"], printing the reason. Claude keeps the field for context but Python enforces the rule.

Show me the diff before writing.
```

### PROMPT 1.10 — Housekeeping bundle (low risk, one pass)

```
Small cleanups in morning_crew.py, no behaviour changes except where stated:

1. run_news_research() currently queries DuckDuckGo for ALL watchlist symbols (~110 queries) although only signal symbols' news reaches Claude. Change main() to call it with: signal symbols + symbols of OPEN positions + "INDIA_VIX" only. Update the docstring.
2. Remove the unused module-level "ddgs = DDGS()" instance in run_news_research() (each retry already builds its own client).
3. In synthesize_with_claude(), the example JSON in the prompt shows "score": 7 and reasoning "Score 7/9..." — stale V8 text inside a V9 prompt. Change the example to "score": 11 and reasoning "Score 11/14, RSI 54 in sweet spot, no earnings, sector clear" so the model is not anchored to the old scale.
4. Ask Claude for rejections too: extend the required output schema to {"approved": [...existing array...], "rejected": [{"symbol", "reason"}]}. Update the parsing to accept BOTH the old bare-array format and the new object format (backward compatible). In write_decision_log(), use the rejected list to fill the "skipped" reasons — the current next() lookup over `approved` can never find a reason for a skipped symbol (it filters those out first), so every skip logs the default string.
5. atm_strike = round(price / 20) * 20 is wrong for most underlyings (strike steps vary). Since options are disabled (equity-only matrix), set atm_strike to None for EQUITY instruments and add a TODO: "derive from Angel One instrument master when options re-enabled".

Show me the diff before writing.
```

---

## PHASE 2 — Structural (next 2 weeks; plan-first prompts)

### PROMPT 2.1 — Unified scoring engine + the mandatory no-lookahead test

```
Goal: one source of truth for the V9 score, per CLAUDE.md's engine/ layout. morning_scan.py and the backtest engines each re-implement scoring, which guarantees drift.

PLAN FIRST — show me the design before touching any file:
1. Create engine/__init__.py and engine/scoring.py containing pure functions, no I/O, no network:
   - compute_indicators(df) -> df with ma20/ma50, rsi14 (Wilder), atr14 (Wilder), obv, vol_ratio, pct_range20, 52w high/low columns
   - bucket_scores(df_row_or_df, rs_rank, regime_pts) -> dict of the six buckets per Notes/03 Part A1 (Trend 3, RS+52W 4, Oscillator 1, Volume 3, Volatility 1, Regime 2)
   - total_score(...) and mr_signal(...) (current thresholds, even though Notes/05 §3 shows MR never fires — flag it in a docstring referencing the report)
   The functions must work both on the latest row (live scan) and vectorised over a whole DataFrame (backtest).
2. Refactor morning_scan.py analyse_stock() to call engine.scoring; outputs must remain byte-identical (same JSON fields, same rounding).
3. Create tests/test_scoring_no_lookahead.py (pytest): for 3 symbols' cached data, assert that the score computed at bar t over data[:t+1] equals the score at bar t computed over data[:t+1+k] for k in (1,5,20) — i.e., appending future bars never changes a past score. Also assert RSI/ATR at bar t are unchanged when future bars are appended. Tests must run offline from a small bundled CSV fixture (create tests/fixtures/ from cached data), under 30 seconds, no network.
4. Do NOT modify the backtest engines yet — that is a later migration once the live scan is proven identical for 5 consecutive days.

After I approve the plan, implement with diffs file by file.
```

### PROMPT 2.2 — Panic flatten script (safety tool, paper-aware)

```
Create C:\trading_bot\panic_flatten.py — the manual "flatten everything now" button:
1. Loads paper_trades.json; for every status OPEN trade, fetches the current price via yfinance (Angel One fallback exactly as position_monitor does, reuse its helper if importable), closes the trade at price * (1 - slippage tier) for LONG (mirror for SHORT), computes costs via cost_model, writes pnl, exit_reason "PANIC_FLATTEN", exit_date/time.
2. Updates performance via the existing update_performance() in position_monitor (import it; do not duplicate).
3. Writes a PANIC_FLATTEN entry to decision_log.json with the list of closed symbols and total P&L.
4. Sets strategy_memory.json circuit_breakers.circuit_active = true with circuit_resets_on = today + 2 trading days, so the crew will not re-enter tomorrow morning.
5. Requires interactive confirmation: print the open positions and ask the user to type FLATTEN before doing anything. Refuse to run if a command-line arg is used to bypass the confirmation.
6. UTF-8 reconfigure + heartbeat instrumentation like other scripts.

Show me the full file before writing.
```

### PROMPT 2.3 — SQLite order-state tracker (PLAN ONLY — no code yet)

```
PLAN ONLY — produce a design document, write it to Notes/07_SQLite_State_Design.md, and change no Python:

Design a SQLite-backed position/order state store at C:\trading_bot\state\trading.db to replace JSON files as the source of truth (JSON stays as nightly human-readable export).
- Tables: orders (id, date, symbol, direction, strategy, instrument, qty, entry/sl/target, state, timestamps), state_transitions (order_id, from_state, to_state, at, reason), performance snapshots.
- State machine: PENDING -> PLACED -> PARTIAL -> FILLED -> CLOSED, plus REJECTED, ERROR, UNKNOWN. Enumerate the COMPLETE transition matrix (every state x every event has exactly one defined outcome) including recovery-after-gap behaviour per the FSM principle in Notes/03 Part B3.1.
- Which script owns which transition (crew creates PENDING; execution engine PLACED/FILLED/REJECTED; monitor CLOSED; watchdog flags UNKNOWN).
- Migration plan from paper_trades.json and rollback plan.
List open questions for me at the end. Do not write any code in this pass.
```

---

## NOT via Gemini CLI (unchanged from Document 02, still true)

1. **Re-registering Task Scheduler jobs & verifying they fire as SYSTEM** (0.B manual step, 0.F smoke test) — you must watch them run.
2. **Telegram bot provisioning** (token, chat id into `.env`).
3. **Verifying statutory cost rates** in `cost_model.py` against a real contract note.
4. **Hand-verifying performance math** after any change to `update_performance()` (Doc 02 Gap 1 rule: never let the tool certify its own fix). Note: the 2019–2026 backtest in Doc 05 already hand-verified `cost_model` to the paisa.
5. **Angel One tokens/lot sizes** for new symbols; **real option-chain data** sourcing.
6. **Live order placement** (P2.3 in Doc 04) — deliberate engineering with review, not a CLI prompt.
7. **MR sleeve redesign decision** — Doc 05 §3 proved the current condition can never fire; *which* replacement (RSI<45, RSI(2)<10, or MA200 qualifier) is a strategy decision to shadow-test first, not a code edit to rush. When you've chosen, I'll write that prompt.

---

## Verification checklist (run after each phase)

| After | Check |
|---|---|
| 0.A–0.C | `venv\Scripts\python.exe -c "import yfinance, pyotp, anthropic, ddgs, ta"` exits clean; both smoke-test tasks end without traceback |
| 0.D | Stop one task manually mid-day → watchdog Telegram message arrives |
| 0.E | 3 stuck trades CLOSED with `data_quality` tag; `performance_live` stats still exclude them |
| 1.1–1.5 | Run `morning_scan.py` manually: F1-veto line prints on a red day; no signal has avg value < ₹5cr; `strategy_memory.json` shows 3 / 5% / 25000 |
| 1.6 | Scan runtime roughly halves; `data_cache\hist_*.pkl` appears; second run same morning hits cache |
| 1.8 | `shadow_signals.json` grows daily; `shadow_monitor.py` closes entries; `shadow_performance()` prints buckets |
| 2.1 | `pytest -x tests\` green in <30s; scan output byte-identical for 5 consecutive days before backtests migrate |
