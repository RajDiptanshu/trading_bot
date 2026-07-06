# NSE Recommendation Engine

TradingView-style research app: click any watchlist stock → three agents work together → a
buy/sell/wait blueprint with entry zone, stop, target, conviction, reasons **for and against**.

```
Agent 1  Technical      pure Python — V9 14-pt score, ATR levels, RS rank, regime   (no AI)
Agent 2  Fundamental    yfinance + DuckDuckGo news, kill-words, earnings, ROE/D-E   (no AI)
Agent 3  Strategist     ONE Claude call — applies Notes\03_Strategy_Playbook rules  (veto/structure only)
Agent 4  Executor       pure Python — paper-trades Rs 20L: sizing, stops, trail,    (no AI)
                        kill switches, options (defined-risk), full decision log
```

## Agent 4 — Trade Executor (paper, Rs 20,00,000)
`agent4.py` runs the paper portfolio like a maths-only desk trader. UI: **💼 Portfolio** button.

- **Entry cycle** (`POST /api/portfolio/execute` or `python agent4.py execute`): regime gates
  (F1 veto, VIX EXTREME, kill switches) → scan → top-5 LONG candidates → full 3-agent
  recommendation → enter if BUY with conviction ≥ 0.35.
- **Sizing**: risk = min(1% of equity, Agent 1's vol-adjusted %, Rs 20k); qty = risk / (entry − stop);
  20% position-value cap; 5% total portfolio heat cap; max 6 positions.
- **Monitoring** (`POST /api/portfolio/monitor` or `python agent4.py monitor`, run once after close):
  hard stop → bank half at +3×ATR & move stop to breakeven → chandelier trail → day-12 time stop
  (only if never +1×ATR) → options expiry exit (4 DTE).
- **Kill switches**: day loss ≥ 2% halts new entries (auto-resets next day); equity 10% below peak
  halts until manual reset (`POST /api/portfolio/reset-halt`).
- **Options** (defined-risk only, never naked): taken only when conviction ≥ 0.55 AND real NSE
  option-chain premiums are fetchable AND a real lot size exists AND one lot's max loss fits the
  risk budget (Rs 20k at 20L capital) — otherwise it falls back to EQUITY and logs why.
- **Index sleeve (NIFTY + BANKNIFTY)**: each entry cycle also scores both indices on a 7-point
  trend score (MA20>MA50 stack, MA50 slope, 20d breakout, 63d momentum, RSI zone, ADX). Score ≥ 5
  → defined-risk spread from the real NSE chain (bull call spread normally; bull put credit spread
  only when VIX percentile > 70 AND falling; nothing when vol is rising — no naked anything, no
  fallback). Lots: NIFTY 65 / BANKNIFTY 30 (Jan-2026 NSE circular — VERIFY if circulars change).
  **SENSEX is intentionally absent**: its options trade on BSE, which the NSE chain fetcher cannot
  reach, and Nifty/Sensex are ~0.99 correlated so the exposure would be redundant.
- **State**: `C:\trading_bot\agent4_state.json`. **Every decision** (entered or skipped, with full
  feature snapshot) → `C:\trading_bot\agent4_decisions.jsonl` — this is the future ML dataset.

Honest caveats: paper fills assume 0.1% equity / 1% option slippage; stops are checked against the
daily bar (a gap through the stop fills AT the stop here, real fills would be worse); the NSE chain
endpoint is unofficial and breaks often (the code degrades to equity, never fabricates premiums);
yfinance closes are delayed. Judge nothing before ~30 closed trades.

All numbers are computed by Python. Claude can veto or downgrade — never invent prices.
If `ANTHROPIC_API_KEY` is missing or the API call fails, a deterministic **rules-only**
strategist produces the same schema (labelled in the UI), so the app always works.

## Run

```cmd
cd "C:\trading_bot\Algo Trading\recommender"
start.bat
```

or manually: `C:\trading_bot\venv\Scripts\python.exe app.py` → http://127.0.0.1:8650

Reads `watchlist.json` and `.env` from `C:\trading_bot` (override with env var `TRADING_BOT_DIR`).

## Features
- Dark TradingView-style UI; candlestick chart (lightweight-charts) with MA20/50, volume,
  and SL/Target lines drawn after a recommendation.
- Live regime banner: Nifty vs 50/200-dma, India VIX tier, today's minimum score.
- **⊞ Scan all**: scores the entire watchlist in parallel (the morning-scan table, sortable).
- **＋ Add stock**: any NSE symbol — validated against live data, appended to
  `C:\trading_bot\watchlist.json` (the same single source of truth your bot uses).
- Agent pipeline status while a recommendation is generated.
- 10-min price cache / 30-min RS-rank cache so clicks stay fast.

## API (for your own scripts)
| Endpoint | What |
|---|---|
| `GET /api/regime` | Nifty regime + VIX tier |
| `GET /api/watchlist` | symbols, sectors, RS ranks |
| `POST /api/watchlist/add` | `{"symbol","sector"}` |
| `GET /api/stock/{sym}` | Agent 1 output (score, buckets, levels) |
| `GET /api/chart/{sym}?period=13mo` | OHLCV + MAs for the chart |
| `GET /api/news/{sym}` | Agent 2 output |
| `POST /api/recommend/{sym}` | full 3-agent recommendation |
| `GET /api/scan` | whole-watchlist scan |

## Honest limitations (v1)
- Data = Yahoo Finance (delayed, occasionally gappy). Angel One can replace it later via the
  same `history()` function in `agents.py`.
- Options suggestions use India VIX only — per-stock IV/option-chain pricing is not wired in
  yet (see Notes\05 §5b before trusting any options idea).
- Earnings dates from yfinance are approximate; verify before events.
- This is a research tool for paper trading. Not investment advice.
