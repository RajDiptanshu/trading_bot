---
name: nse-algo-trading-project
description: Full state-of-project reference for Diptanshu's NSE algo-trading system (recommender app + Agents 1-5, paper portfolio, schedule, strategy rules, conventions). Read this FIRST in any new chat about the trading bot — it replaces re-reading the codebase and old conversations.
---

# NSE Algo-Trading Project — State of the World
*Last updated: 2026-07-13 (V11.1: seed-ensemble ML entry gate — DEFAULT OFF, §6b; V11 two books §5e; V10.3 IV/Greeks+ITM §5d; independent-data validation + tilt falsified §5f). Update this file whenever architecture, risk rules, or schedule change.*

## 1. What this project is
A fully-local NSE swing-trading system on Diptanshu's Windows machine. Research UI + agents
generate and execute PAPER trades autonomously via Windows Task Scheduler. [V11 2026-07-12]
TWO SEPARATE BOOKS: an EQUITY-only book (Rs 10,00,000) and an F&O-only OPTIONS book
(Rs 10,00,000) — see §5e; the sleeves are tested independently. No real orders are placed
anywhere in the code. Goal: 3-6 months of honest paper results
(gates in §9, tracked PER BOOK) before any real-money discussion. ML4T (Stefan Jansen, repo at
`C:\trading_bot\machine-learning-for-trading`) is the reference text ("bible") for the ML layer.

## 2. File map (don't re-explore; this is current)
```
C:\trading_bot\                       (TRADING_BOT_DIR — single source of truth)
  watchlist.json                      55 NSE stocks w/ sector + F&O lot_size (also fed by UI Add)
  news_map.json                       Agent 6 contagion playbook: sector→bellwethers/thesis/cues + macro_drivers
  universe_cache\universe.json        merged NSE universe (Nifty 50/Bank/Midcap150/500 + curated); CSVs cached alongside
  news_briefs\latest.json             today's pre-market briefing (+ news_brief_YYYY-MM-DD.json archive)
  run_news_brain.bat                  manual News Brain build; setup_news_brain_task.ps1 registers 08:30 task
  strategy_memory.json                kill-words + live_stats override for EV/Kelly
  .env                                ANTHROPIC_API_KEY etc. (app.py AND agent4.py load it)
  cost_model.py                       audited NSE costs (equity delivery + options), both sides
  agent4_equity_state.json            [V11] EQUITY book (10L; shares only)
  agent4_options_state.json           [V11] OPTIONS book (10L; F&O only)
  agent4_state.legacy-20260712.json   archived pre-split 20L book (final equity 20,35,489)
  agent4_decisions.jsonl              EVERY decision w/ feature snapshot (+ "book" field V11)
  setup_agent4_tasks.ps1              Task Scheduler migration (run once as admin)
  logs\agent4_entry.log, agent4_monitor.log, ml_retrain.log
  Notes\01..09_*.md                   project docs; 03=Strategy Playbook, 05=Backtest report,
                                      07=V10 agent spec, 09=ML4T usage + 6-month roadmap
  machine-learning-for-trading\       ML4T repo clone
  venv\                               THE interpreter: C:\trading_bot\venv\Scripts\python.exe
  [legacy V9 root scripts: morning_scan.py, position_monitor.py, etc. — RETIRED, tasks removed,
   files kept for reference. paper_trades.json = old V9 ledger, separate from agent4_state.]

C:\trading_bot\Algo Trading\recommender\   (the live system)
  app.py            FastAPI :8650, serves UI + all endpoints (run via start.bat)
  agents.py         Agents 1-3 (V10) + Agent 6 hooks (recommend() loads briefing, _news_veto_reason)
  agent4.py         Agent 4 executor + CLI (execute|monitor|summary|reset-halt); logs news ctx
  news_brain.py     Agent 6 News Brain: build|show|dry CLI -> news_briefs\latest.json
  agent5_report.py  Agent 5 Reporter: build|show CLI -> daily_reports\report_*.json (+ Claude EOD note)
  chat_analyst.py   Conversational analyst (2026-07-07): agentic Claude tool-use loop over the
                    system's OWN functions — 8 tools (price/technicals/news/briefing/full
                    recommendation/option chain/screener/portfolio). GROUNDING RULE: every number
                    must come from a tool result; tool failures are reported, never guessed.
                    POST /api/chat (stateless — client sends history) + 💬 Analyst tab in the UI
                    (tool-audit chips under each reply). Plain-text replies (UI renders no markdown).
                    Env: CHAT_MODEL (defaults to STRATEGIST_MODEL). ~1-4 Claude calls/question.
  news_sources.py   NewsSource ABC + free impls (RSS/GDELT/yfinance/DDG); paid-pluggable
  universe.py       NSE index-constituent loader (Nifty 50/Bank/Midcap150/500 from NSE
                    archives CSVs); merges with watchlist (curated wins); load_universe(scope)
  screener_engine.py  scan_universe(scope) — bulk prefetch + universe RS + parallel technical
                    funnel + News Brain overlay; NO Claude in bulk scan (cost stays bounded)
  ml_signal.py      ML4T feature/model/CV code; train_ml.py trains -> models/ml_signal.pkl
  options_math.py   Black-Scholes IV + Greeks (pure stdlib, no scipy): inverts Angel premiums
                    to IV, computes delta/gamma/theta/vega; enrich_chain() drives the ITM
                    long-leg pick and the option_chain_history.jsonl IV dataset [V10.3]
  static\index.html single-file React UI (in-browser babel; no build step)
  README.md         user-facing docs incl. Agent 4 section
  remote_access.bat + REMOTE_ACCESS.md  [2026-07-12] reach the cockpit from any device via
                    Tailscale (private mesh — app stays 127.0.0.1, `tailscale serve` proxies
                    the tailnet to it; NO public tunnel — app has no auth). APP_HOST env
                    (default 127.0.0.1) only widens the bind for the direct-IP fallback.
```

## 3. Agent architecture + status
| Agent | What | Status |
|---|---|---|
| 1 Technical (`technical_agent`) | pure Python. V9 14-pt score + V10: Kestner confirmations (40d recency, range expansion, 12-1 momentum>0, need >=2/3), ADX, RSI(2) MR sleeve (SHADOW-ONLY), F1 hard veto, momentum-laggard veto (RS<40), hybrid exit plan, half-Kelly sizing | LIVE |
| 2 Fundamental (`fundamental_agent`) | yfinance news STRICT last-NEWS_MAX_AGE_HOURS (default 48; undated items DROPPED — yf nests time in content.pubDate, old `not ts` leaked stale news; DDG removed = untimestamped; each headline carries ts+age_h), LM-style word-list sentiment, kill-words, earnings window T-5..T+1 veto / T+10 caution, quality 0-4. No recent news ⇒ empty (UI shows "no news in last Nh") | LIVE (recency fix 2026-06-22) |
| 3 Strategist (`strategist_agent`) | ONE Claude call (claude-sonnet-4-6), strict JSON, EV-first playbook, Cohen vol-matrix for options instrument, sanitizer enforces hard floors; deterministic `_rules_only` fallback when no API key | LIVE |
| 4 Executor (`agent4.py`) | paper trades Rs 20L; full lifecycle below | LIVE, tested 33/33 (mocked harness) |
| 5 Reporter (`agent5_report.py`) | daily report: reuses agent4.summary() + adds realized/unrealized P&L split, payoff ratio, avg holding, current DD, today's activity, GO-LIVE GATE scorecard (>=50 trades · PF>1.3 · maxDD<10% · +EV), Claude EOD note. /api/report + cockpit Report tab (equity-vs-NIFTY chart). Scheduled Agent5_Report 15:50. | LIVE (2026-06-23) |
| 6 News Brain (`news_brain.py`) | PRE-MARKET 24h news synthesis. Sources via `news_sources.py` (NewsSource ABC: ET/Livemint RSS + GDELT + yfinance + DDG, free now / paid-pluggable). ONE Claude call reads headlines through the contagion map (`news_map.json`) → briefing: market_bias, per-sector bias+reason, stock_flags, +160 source headlines for audit. Deterministic keyword fallback if no API key. Output → `news_briefs/latest.json`. Wired into Agent 3 (`recommend`→`strategist_agent`): NEGATIVE stock flag or sector_bias NEGATIVE@conf≥0.6 (e.g. Accenture→NSE IT) VETOES new longs that day. News may only veto/downgrade. | LIVE (2026-06-20) |
| Cockpit UI | single-file React (static/index.html): Screener tab (universe table + scope/sector/score/LONG filters, scan cached ~10min, click→stock), News tab (market bias + sector contagion + stock flags from /api/news-brief), Stock view (chart+technicals+rec), Portfolio | LIVE (2026-06-22) |

## 4. Agent 4 — the exact rules (agent4.py constants)
- [V11] TWO BOOKS (see §5e). EQUITY book: capital 10,00,000, risk/trade = min(1% book equity,
  Agent-1 vol-adjusted %, Rs 20k cap), max 12 positions. OPTIONS book: capital 10,00,000,
  risk/trade = min(2% book equity, Rs 20k cap) — Agent-1's vol % deliberately NOT applied
  (defined-risk structures; budget IS the max loss), max 5 positions. Heat 8% / kill
  switches / 20% value cap apply PER BOOK. Pre-V11 text below reads "the book" as the
  single 20L book; the mechanics are unchanged per book unless §5e says otherwise.
- [V10.1 2026-07-06, user decision "trade as much as the logic allows"]: max 12 positions
  (AGENT4_MAX_POSITIONS), heat cap 8% (AGENT4_MAX_HEAT_PCT), 5 option slots
  (AGENT4_MAX_OPTION_POS); 20% value cap/position unchanged. NOTE heat is the true throttle
  (~8 full-risk positions at Rs20k risk each). Kill switches unchanged. Book carries
  config_version=V10.1 marker (cutover 2026-07-06, admin entry in decisions log);
  pre/post-V10.1 trades are segmentable for the 4-week review.
- Entry cycle: blocked if halted / F1 false / VIX EXTREME / max positions. Else scan the
  UNIVERSE (env AGENT4_UNIVERSE_SCOPE, default nifty210 ~204; core/nifty50/nifty500 via
  universe.py) with bulk prefetch + universe-relative RS + parallel technical scan ->
  LONG + liquid candidates sorted by score -> top TOP_N_RECS (env AGENT4_TOP_N_RECS, default 5)
  get full 3-agent rec -> enter if action BUY and conviction >= 0.35. New (non-curated)
  names route EQUITY-only (no verified F&O lot). SPEED LEVERS: smaller scope, lower TOP_N_RECS;
  anthropic client bounded to 60s/1 retry, yfinance bulk fetch 20s (a network blip fails fast,
  doesn't hang). Awake-machine scan: ~19s for 50 names, ~40s for 204. [2026-06-20]
- Exits (priority): hard stop (intraday low <= stop, fills AT stop) -> bank HALF at +3*ATR and
  stop->breakeven -> chandelier trail on remainder (highest close - 3*ATR, ratchet only) ->
  day-12 time stop ONLY if never reached +1*ATR. Options additionally: exit at underlying
  stop/target, and always by 4 calendar days to expiry.
- Kill switches: day realized loss >= 2% equity -> halt new entries, auto-resets next day;
  equity < peak*0.90 -> halt until manual reset (UI button / `reset-halt`).
- Options: only ATM_CALL / BULL_CALL_SPREAD / BULL_PUT_SPREAD (defined-risk, never naked),
  only if conviction >= 0.55 AND real option-chain premiums fetched AND lot_size > 1
  AND one lot's max loss <= risk budget; else EQUITY fallback with reason logged. Max 3
  option positions. OPTION CHAIN (2026-06-23): now from ANGEL ONE — live_quotes.build_option_chain
  reads the Angel instrument master (option tokens/strikes/expiries + lot sizes for all ~234 F&O
  names) + getMarketData("FULL", NFO tokens) for live LTP/OI/bid-ask; unofficial NSE endpoint is
  the fallback (_chain_from_nse). _lot_size() consults the master so expanded-universe F&O names
  (not just the 55) can trade options. strict leg filter: OI>0 + bid-ask <= MAX_LEG_SPREAD_PCT.
  Failures degrade to equity, never fabricate premiums. Slippage: 0.1% equity, 1% option premium.
- INDEX SLEEVE (added 2026-06-11): NIFTY (^NSEI, lot 65) + BANKNIFTY (^NSEBANK, lot 30 —
  Jan-2026 circular, VERIFY on change) trade as defined-risk spreads in every entry cycle.
  Signal = own 7-pt trend score (`_index_signal`: stack 2pt, MA50 slope, 20d breakout, 63d
  momentum, RSI 45-70, ADX>=18), need >= 5 (INDEX_MIN_SCORE). Instrument: bull call spread,
  or bull put credit spread only if VIX pctile>70 & falling; vol rising = skip (no fallback
  exists for an index). Positions carry a "yahoo" field; `_bar()` routes index data via
  `_index_history()`. SENSEX deliberately excluded: BSE options unreachable by the NSE chain
  fetcher + ~0.99 correlation with NIFTY makes it redundant; revisit only with a BSE API.
- State writes are atomic; a book with trade history is NEVER auto-rewritten on capital change.

## 5. Schedule (Windows Task Scheduler, \TradingBot\, Mon-Fri IST)
NewsBrain_Premarket: `news_brain.py build` 08:30 (24h news + Claude contagion synthesis →
news_briefs\latest.json; ready before the 09:25 cycle so recommend() reads it. Register via
`setup_news_brain_task.ps1` as admin; ~1-2 min/run; logs in logs\news_brain.log) ·
Agent4_Cycle30: `agent4.py cycle` every 30 min 09:25→15:25 (13 runs; exits FIRST, then
entries if capacity; internal gate refuses outside 09:15-15:35; Telegram digest only when
trades happen; SAME-DAY RE-ENTRY GUARD: symbols exited today can't be re-bought today) ·
15:45 Agent4_MonitorClose (`agent4.py monitor`, final daily candle + equity-curve point) ·
15:50 Agent5_Report (`agent5_report.py build` — daily report + gate scorecard + Claude EOD note
→ daily_reports\; also snapshots NIFTY/BANKNIFTY + open-option chains → option_chain_history.jsonl
and aggregates the option_funnel from today's decisions) ·
Sun 18:30 WeeklyTune (`weekly_tune.py` — refresh both price panels from live data, re-run V10.1
config on 55@conf2 + nifty210@conf3 full/IS/OOS, compare paper book vs OOS expectation, update
strategy_memory.live_stats only at >=30 closed trades → backtest_results\tuning_log.jsonl;
4-WEEK REVIEW MILESTONE: 2026-08-03) ·
16:05 Agent4_Watchdog (heartbeat check, alerts) · Sun 18:00 ML_WeeklyRetrain.
Old Agent4_EntryCycle / Agent4_MonitorMidday names retired (removal list in ps1).
All via cmd wrapper, venv python, cwd = recommender, PYTHONIOENCODING=utf-8, logs in
`C:\trading_bot\logs\`. Old V9 tasks (MorningScan/MorningCrew/ExecutionEngine/MidSentinel/
PositionMonitor/WeeklyTuner/Watchdog*, in \TradingBot\ AND root TradingBot_*) are REMOVED by
setup_agent4_tasks.ps1. WakeToRun + StartWhenAvailable set; PC must be on/asleep-wakeable.

## 5b. V10.1 (2026-07-06) — backtest-lab loop results (backtest_lab.py / ml_lab.py / uni_diag)
Full review→backtest→improve loop, IS(2019-23)/OOS(2024-26) discipline, real NSE costs.
FINDINGS: (1) V10 base held on fresh data (PF 2.38 full) BUT OOS-only = PF 1.51 — the honest
recent-period number. (2) Broad 204-name universe at V10 params = PF 1.02 (OOS 0.90 NEGATIVE):
much of the edge was the CURATED WATCHLIST (+Rs1,264/trade curated vs -Rs215 non-curated).
(3) stop1.5 looked best IS (PF 3.57) and collapsed OOS (1.01) — curve-fit trap caught.
CHANGES SHIPPED (each validated OOS): initial stop 2*ATR→2.5*ATR + time-stop day-12→day-8
(55-name OOS PF 1.51→2.02, exp Rs529→809, Sharpe 2.25→4.12); TIERED CONF GATE in agent4 —
non-curated universe names need ALL 3 Kestner confirmations (broad-universe OOS 0.90→1.56,
IS≈OOS stable), curated keep conf≥2. ML: FWD_DAYS 5→10 + train/infer on the nifty210
UNIVERSE (~200 names) — 55-name IC was -0.011 (noise-gated); 204-name 10d IC +0.037 PASSES
the gate (best fold most recent, +0.072). Breadth, not features, was the ML fix.
Caveats: OOS samples 84-145 trades; universe backtests carry survivorship bias
(today's constituents on their own past); index-sleeve params unchanged (not re-validated).

## 5c. V10.2 (2026-07-10) — options RCA + exit fix + expert chat
OPTIONS RCA: both closed option spreads (NIFTY/BANKNIFTY bull-call) exited UNDERLYING_STOP_HIT at
2 days for -Rs28,803 (~96% of max loss each) — vs -Rs15,589 on 5 equity trades. ROOT CAUSE: the
2.5*ATR equity stop was applied to defined-risk debit spreads (whose max loss is ALREADY the debit),
so a 2-day underlying wiggle crystallised near-max loss and killed all recovery. FIX (run_monitor
options branch): removed the underlying noise-stop; spreads now managed on (1) SPREAD_TARGET —
bank at OPT_PROFIT_TAKE_FRAC (0.6) of max profit on the spread's own mark, (2) EXPIRY_EXIT
(OPT_EXIT_DTE), (3) UNDERLYING_THESIS_BREAK — only on underlying CLOSE past OPT_DISASTER_ATR (4.0)
ATR. Max loss stays capped at the debit; losers get theta/recovery time. Verified on live spreads
(NIFTY spread was +Rs65/sh and would've been noise-stopped under old logic).
DONE 2026-07-11 (§5d V10.3): ITM long leg + in-house IV/Greeks. STILL-NOT-DONE: per-stock
IV-percentile gating (don't buy rich premium — the enriched history is now accruing the dataset
for it), bearish/neutral structures (book is 100% long-delta). CHAT ANALYST (chat_analyst.py): added
Anthropic native web_search (server tool, India user_location) + a 15+yr-analyst persona that
mandates MOVE/VERDICT/LEVELS/WATCH with a stop+target every time — fixed the "no news driver" punt
(now finds real catalysts, e.g. TRENT's Q1 miss, with levels).

## 5d. V10.3 (2026-07-11) — in-house IV/Greeks + ITM long leg
Angel's option chain ships premiums only (iv=null), so `options_math.py` (pure stdlib, no scipy)
inverts Black-Scholes from each live premium to recover IV, then computes delta/gamma/theta/vega
(theta per calendar day, vega per 1 vol-pt; NSE-fallback percent IV used only when the premium is
all-intrinsic and the solve is indeterminate). `fetch_option_chain` now enriches every node before
caching+logging, so `option_chain_history.jsonl` accretes the IV/Greeks dataset the deferred
per-stock IV-percentile gating needs. LONG CALL LEG moved ATM→ITM: the BUY leg of ATM_CALL /
BULL_CALL_SPREAD (single-stock AND index sleeve) is now the ~0.65-delta strike
(AGENT4_OPT_LONG_DELTA; OPT_RISK_FREE_RATE default 0.065, both env-tunable) to cut theta while
max-loss stays the debit; graceful ATM fallback if the chain carries no Greeks or the ITM strike is
untradeable under the strict OI/spread filters. Positions record `long_delta`. VERIFIED LIVE: NIFTY
BULL_CALL_SPREAD picked 24050/24950 at long-delta 0.666, spot 24207 (was ATM); IV solved 0.105 from
Angel LTP where the feed gave null. Sizing unchanged (lots = risk_budget / max-loss) — an ITM long
costs more so fewer lots/trade, a deliberate theta-for-cost trade. NOT backtested (no historical NSE
option data exists; these snapshots are the forward dataset). BULL_PUT_SPREAD unchanged (short ATM
put + protective lower put — no long call to move).
ATM COUNTERFACTUAL: every option entry — and every "ITM too rich to fit 1 lot" fallback/skip —
logs `atm_counterfactual` {long/short strike+premium, long_delta, per-share debit/max-loss,
lots_same_risk} onto the position AND the decisions log, giving a from-day-one ITM-vs-ATM record
with no separate backtest. LIVE FINDING (NIFTY, spot 24207): ITM 24050/24950 debit 322/sh vs ATM
24200/24950 debit 230/sh (+40%); ITM max-loss Rs20,946/lot EXCEEDS the ~19-20k index risk budget,
so that spread now SKIPS (indices have no equity fallback). Expect the index option sleeve to quiet
down under ITM — levers: shallower AGENT4_OPT_LONG_DELTA (0.60), a higher option-only risk cap, or
ITM-for-stocks-only. The counterfactual log will measure how often this bites.
PAPER-BOOK RESET 2026-07-12: the 5 pre-V10.3 option trades (3 open ATM bull-call spreads + the 2
UNDERLYING_STOP_HIT losers totalling -Rs28,803) were VOIDED as-if-never-entered (cash reconciled
+Rs81,318 to 191,045; backup agent4_state.backup-20260712-001200.json; ADMIN_VOID_OPTIONS marker in
the append-only decisions log). Options sleeve restarts flat on V10.3 logic; equity sleeve untouched
(5 closed trades, -Rs15,589). Gate closed-trade count dropped accordingly (options now 0).

## 5e. V11 (2026-07-12) — TWO SEPARATE BOOKS (user decision: test sleeves independently)
EQUITY book (agent4_equity_state.json, Rs 10L): EQUITY instrument ONLY — option code paths are
never consulted (no chains/legs/Greeks), no index sleeve, risk 1%/trade, max 12 positions.
OPTIONS book (agent4_options_state.json, Rs 10L): F&O ONLY — stock defined-risk spreads
(conviction>=0.55) + the NIFTY/BANKNIFTY index sleeve; an entry that cannot be built as an
option structure SKIPS (chain down / illiquid / EQUITY proposal / lots<1) — NO equity fallback;
risk 2%/trade (Rs20k on 10L: option max-loss runs Rs15-21k/lot, a 1% book would sit near-silent;
user picked 2% explicitly). NO FUTURES yet (user deferred). One process still runs both books
per cycle — exits first then entries, ONE scan + ONE Claude rec per candidate shared by both
books (the same signal becomes a paired equity-vs-options test; Claude cost unchanged). The
same-day re-entry guard, kill switches, heat, peaks, curves are PER BOOK; one book halting does
not stop the other. Decisions log lines carry "book"; option-funnel (signals->entered->skip
reasons) reported daily by Agent 5. Schedule/tasks UNCHANGED (same CLI verbs; reset-halt takes
an optional book arg; /api/portfolio returns {books,combined}; /api/portfolio/reset-halt?book=).
UI: Portfolio tab = combined header + two bordered book sections; Report tab = per-book report
blocks + options funnel. summary() => {books:{equity,options}, combined}, each book block keeps
the old field shape. MIGRATION 2026-07-12: the 7 open legacy equity positions were closed at the
last market price (2026-07-10 close, BOOK_SPLIT_RESTRUCTURE, +Rs51,074 on the closes), the 20L
book archived to agent4_state.legacy-20260712.json (final equity Rs 20,35,489, +1.77% lifetime
incl. the V10.3 options void), ADMIN_BOOK_SPLIT marker in the decisions log, both new books
started flat at 10L. GATES (§9) now count PER BOOK from zero — the ~50-trade clock restarted.
weekly_tune.paper_vs_backtest now compares the EQUITY book (its backtests are the equity
strategy; live_stats/EV-Kelly prior stays equity-only — verified before the Sun 18:30 run).
Tested: 25-check mocked harness (book walls, skip-not-fallback, per-book kill switches +
reset, budgets 10k/20k, summary shapes, book-tagged decisions) ALL PASS + live smoke on :8651
(both UI tabs render, no console errors). NOTE: restart the :8650 app (start.bat) to load V11 —
the running server still holds the pre-split module.

## 5f. Independent-data equity validation (2026-07-12) — recent OOS holds, large-cap tilt FALSIFIED
Two user-provided external datasets in C:\trading_bot cross-check the EQUITY edge. NEITHER helps
options (no premiums/IV/strikes, no NIFTY/BANKNIFTY series) — the options-data gap is unchanged.
  • `NSE500 Daily and Intraday Stock data\` (497 per-stock xlsx, Day + intraday sheets): RAW/
    as-traded — a constant per-stock scale vs live (VBL 2.5x, RELIANCE 2.0x), HARMLESS for
    %-return backtests (notional & %-of-turnover costs invariant). Daily 2018→Jun-2024.
    Split-consistent: the only >25% overnight jumps were REAL crashes (COVID 2020-03-23,
    Hindenburg 2023-02). Intraday shallow.
  • `Nifty 500 (NSE) Stocks Multi-Timeframe Dataset\` (7 consolidated CSVs, one per timeframe,
    long format Datetime,Ticker,OHLCV): SPLIT-ADJUSTED (= live; RELIANCE 2024-06 = 1477 = raw
    2955 halved for the Oct-2024 1:1 bonus). Daily 2005/2019→Feb-2026, 501 stocks. Intraday
    tapers (1h from 2023, 30m Aug-24, 15m May-25, 5m Nov-25, 1m ~3wks) — only 1h has backtest
    depth. THE better daily source: adjusted AND reaches the 2024-2026 OOS the xlsx set can't.
RECENT OOS (V10.1 rules, nifty210 radar, 195 adjusted MTF names, split at 2024-01-01):
  conf2  IS PF 1.46 (n147) ≈ OOS PF 1.42 (n77) exp Rs413 — edge HOLDS out-of-sample on
         independent adjusted data with ~ZERO decay (not curve-fit; clears the PF>1.3 gate).
  conf3  IS 1.33 ≈ OOS 1.33 — the tiered gate does NOT lift PF on this adjusted set (its benefit
         is source/window-dependent, not robust; keep it, don't over-trust it).
  HONEST CALIBRATION: the broad-universe edge is ~PF 1.4, NOT the ~2.0-2.5 the curated-55/raw
  runs flattered. Treat backtest PF as "~1.4 broad, ~2 curated, ±0.5 by data source".
LARGE-CAP TILT FALSIFIED (don't re-litigate): baseline OOS per-tier looked like NIFTY50 PF 3.44
  (n22) vs MIDCAP 1.08 (n55) — but 3.44 is a SELECTION artifact (the top 22 large caps that beat
  midcaps for slots). Forcing nifty50-only, or boosting N50 score to crowd out midcaps, gives OOS
  PF 1.42 on 73 trades = SAME as broad and LOWER total P&L (Rs342x73 < Rs413x77). Every tilt
  raised IS (→2.0-2.13) but OOS stayed 1.42 (overfit caught by the split); a partial +2 boost was
  WORSE (OOS 1.30 — let 4 dud midcaps in at PF 0.02). CONCLUSION: keep the broad nifty210 radar at
  conf2; do NOT tilt to large caps, do NOT restrict the universe, do NOT expand to smallcaps
  (survivorship-inflated, §5b). The current config IS the honest optimum. NO agent4 change made.
  Caches in backtest_results\: prices_nse500data.pkl (52 curated raw), prices_n210b/n500.pkl
  (raw broad), prices_mtf210.pkl (adjusted, the recent-OOS cache — reuse this one).

## 6. ML layer (ML4T) — exact lineage
Features ch4 (19, incl. 12-1 momentum, alpha#101), cross-sectional rank transform per date;
label = beats watchlist median fwd 5d; LightGBM->HGB->logistic fallback chain ch11/12;
purged walk-forward CV (5d gap) per ML4T utils MultipleTimeSeriesCV; metrics AUC + daily
rank-IC. Inference = probability used ONLY as veto (<0.40 forces WAIT) / conviction tilt
(>0.60 supports). NEVER train on the few paper trades — history trains, paper validates.
STATUS 2026-06-11: first training DONE (55 syms, 7y, 78,534 obs) → mean AUC 0.4896,
mean rank-IC -0.0059 = NO SIGNAL. A NOISE GATE now lives in ml_signal.score_universe
(MIN_USABLE_IC = 0.01): saved models below it return {} so the system runs ML-free
(ml=None) instead of taking random vetoes. Weekly Sunday retrains continue; the model
switches on automatically only if IC ever clears the gate. Do NOT remove the gate; do not
present ML as active unless meta mean_ic >= 0.01. The validated edge (PF 2.46 ablation)
is the rules system WITHOUT ML.

## 6b. ML-ENTRY investigation + seed-ensemble gate (2026-07-13, V11.1) — the honest arc
Motivated by a diagnostic: the 14-pt technical score does NOT predict returns (bottom-score-third
OOS PF 2.10 vs top-third 1.08 — high score = extended/crowded entry). So can ML rank entries better?
FULL ARC (all on the adjusted 500-name MTF daily data — §5f):
  1. TRAIN/IC: 20-fold walk-forward, mean rank-IC +0.028 (recent half +0.032), positive in 16/20
     folds — real but WEAK signal (AUC ~0.515), and POSITIVE where the score is flat/inverse.
  2. SINGLE-SEED GATE looked amazing (gate ensemble-prob>=0.50 → OOS PF 1.42→1.79) BUT was NOISE:
     a different LightGBM random seed gave 1.06-1.18 (below baseline). Same data, seed 42 vs 7
     swung OOS PF 0.97..2.49. NEVER trust a single-seed ML gate.
  3. SEED-ENSEMBLE RESCUE (10 seeds, mean prob): cancels the seed noise. Gate>=0.50 → OOS PF 2.38,
     IS 1.91 (OOS>IS, no overfit), and TWO independent 5-seed halves BOTH beat baseline at 0.50
     (2.80 / 2.28). 0.50 is the label's own "beat-median" boundary (principled, not tuned).
  4. OUT-OF-PERIOD/UNIVERSE VALIDATION: period-ROBUST on nifty210 (OOS-from-2022 1.01→1.24,
     from-2023 1.29→1.64, from-2024 1.42→2.38; helps most years, hurt only 2023) BUT does NOT
     generalise to nifty500 (full 1.36→1.34). So it's REAL but UNIVERSE-SPECIFIC to the nifty210
     radar we actually trade. Honest magnitude: a consistent +0.2-0.4 PF, biggest recently — the
     2.38 is the optimistic end, not the expectation.
SHIPPED (agent4.py, DEFAULT OFF): AGENT4_ML_GATE=1 makes an EQUITY entry additionally require the
10-model ensemble mean prob >= AGENT4_ML_GATE_PROB (0.50). Options book never gated. FAIL-SAFE: no
ensemble model / no prob for a name ⇒ NOT gated (never blocks). Build the model with
`python train_ml.py --ensemble` → models/ml_ensemble.pkl (ensemble_prob_universe() serves it,
noise-gated at IC 0.01 like the single model). Enable ONLY after a few days of forward paper
validation. Every other tuning idea this session (large-cap tilt, tighter stop, conf3, score-select,
single-seed ML) FAILED OOS — the system sits at ~PF 1.4 broad / ~2 curated; the ensemble gate is
the one thing that survived scrutiny, and only on nifty210.

## 7. API quick reference (app at http://127.0.0.1:8650)
/api/regime · /api/watchlist (+/add POST) · /api/stock/{sym} · /api/chart/{sym} ·
/api/news/{sym} · /api/news-brief (Agent 6 briefing) · POST /api/news-brief/build (slow) ·
POST /api/recommend/{sym} · /api/scan · /api/universe · /api/screener?scope=nifty50|nifty210|nifty500&sector=&min_score=&direction=LONG (universe scan, no Claude) ·
POST /api/chat {messages:[{role,content}...]} (conversational analyst, tool-grounded) ·
/api/chart/{sym}?period=5m|15m|1h|6mo|13mo|3y|5y (intraday = display-only, Yahoo ~15min
delayed; epochs IST-shifted +5:30 in chart_data via _ist_epoch so the lightweight-charts axis
shows NSE hours 09:15-15:30 not UTC — fix 2026-06-22; signals remain daily-bar) ·
/api/portfolio ({books:{equity,options},combined} [V11]) · POST /api/portfolio/execute ·
POST /api/portfolio/monitor · POST /api/portfolio/reset-halt?book=equity|options (omit = both).
UI: 💼 Portfolio view (two book sections) + ⊞ Scan all + per-stock view.

## 8. Data sources + honest limitations (tell the user when relevant)
LIVE QUOTES (added 2026-06-11): `live_quotes.py` — Angel One SmartAPI (creds ANGEL_* in .env,
proven login: SmartConnect + pyotp TOTP; batch getMarketData("FULL") with ltpData and then
yfinance fallback per symbol; index tokens NIFTY 99926000 / BANKNIFTY 99926009 marked VERIFY).
Agent 4 fills at live LTP, re-anchors stop/target to fill (2/3×ATR), refuses entries that ran
>0.5% above the entry zone (chase guard), and `_bar()` uses today's live OHLC for stop checks.
Each position records data_source (live_angel / live_yahoo / daily_delayed).
Remaining limits: signals still computed on daily yfinance history; OPTION CHAIN now Angel One
(instrument master + getMarketData, live LTP/OI/bid-ask; verified 2026-06-23 on RELIANCE/BEL/NIFTY),
unofficial NSE endpoint as fallback; stock-option legs need OI>0 + bid-ask spread
<=5% of mid (MAX_LEG_SPREAD_PCT) else EQUITY fallback; slippage 0.1% equity / 1% index opts /
2% stock opts; earnings dates approximate. BENCHMARK: equity_curve stores nifty; summary().benchmark
gives bot vs NIFTY alpha — negative alpha after enough trades ⇒ index the money instead.
ALERTING: every run writes agent4_heartbeat.json; agent4_watchdog.py (16:05) alerts on missed
runs/kill switches via notify.py (always logs to logs\alerts.log; Telegram if TELEGRAM_BOT_TOKEN
+ TELEGRAM_CHAT_ID in .env); UI shows staleness banner. Operator discipline contract:
Notes\10_Intervention_Rules.md. NO claim of accuracy beyond positive expectancy; "100% accuracy"
is impossible and the user has been told so. Judge nothing under 30 closed trades.

## 9. Roadmap + go-live gates (Notes\09 has detail)
Next builds: analyze-ANY-stock analyst mode; per-stock cockpit news
overlay + fundamentals (connect bigdata-com skills); Kronos zero-shot signal (deferred — needs
separate py3.11 CPU env, see §6); IV history capture from daily chain fetches (LIVE 2026-07-11
via options_math enrichment — §5d; NEXT: use the accruing dataset for per-stock IV-percentile
gating); ablation backtest of ML floor (ch8). Monthly retrain. GO-LIVE GATES (month 6):
>=3 months paper, >=50 trades, PF>1.3 across 2 regimes, maxDD<10%, every kill switch observed
firing. Real money = user's deliberate decision, start 10-20% size. Claude must never place
real-money orders itself. CALIBRATION (§5f): independent split-adjusted OOS 2024-2026 = PF ~1.42
broad / ~2 curated — clears the 1.3 gate but only just on the broad radar, so paper PF near 1.3-1.5
is EXPECTED, not underperformance; judge the live book against ~1.4, not the flattered ~2.5.

## 10. Conventions & gotchas for future Claude sessions
- Always use venv python `C:\trading_bot\venv\Scripts\python.exe`; UTF-8 reconfigure pattern
  at top of entry scripts; Windows paths with spaces need quoting in schedulers.
- All maths in Python; Claude (Agent 3) may only veto/downgrade, never invent numbers.
- watchlist.json is the single universe source; lot_size>1 marks F&O names.
- agent4_decisions.jsonl is append-only — never rewrite; it is the ML dataset.
- Cowork sandbox mounts of this folder can serve STALE/truncated copies — verify via direct
  Read of C:\ paths before trusting `wc`/`grep` from bash; tests were run by copying agent4.py
  + fake `agents.py` into /tmp (harness pattern: mock history/regime/recommend, FakeDF class).
- The old V9 root pipeline is retired — do not "fix" or reschedule it.
- User preferences: concise, truth-first, flag uncertainty, no invented numbers/sources.
