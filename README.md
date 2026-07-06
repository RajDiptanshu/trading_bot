# NSE Algo-Trading System (V10.1)

A fully-local, agentic NSE (Indian equities + F&O) swing-trading system. Six cooperating
agents scan the market, read the news, decide, size, and paper-trade autonomously — with
every number computed in Python and Claude used only for judgment (veto/downgrade), never
for arithmetic. **Paper trading only. No real orders are placed anywhere in this code.**

Built iteratively with Claude Code as a research/engineering partner — this repo is both a
working system and a record of that process (see [`Algo Trading/SKILL.md`](Algo%20Trading/SKILL.md)
for the full architecture log).

## What it does

- **Scans the whole NSE universe** (Nifty 50 → Nifty 500, ~200–500 stocks), not a fixed
  watchlist — a cheap parallel technical funnel ranks everything before any expensive
  (Claude) call runs, so cost stays bounded regardless of universe size.
- **Reads the news before the market opens.** Agent 6 pulls the last 24h of headlines
  (RSS + GDELT + per-stock feeds), maps them through a sector-contagion playbook (e.g. a
  weak Accenture print → NSE IT sector flagged negative), and one Claude call produces a
  structured pre-market briefing that can veto same-day entries.
- **Trades equity or defined-risk options** (ATM calls, bull call/put spreads, never
  naked) once a name clears technical + fundamental + news + conviction gates, using live
  Angel One option-chain premiums — never fabricated numbers.
- **Explains every decision.** Every entry/exit logs the full feature snapshot, the
  reasons for and against, and what would invalidate the thesis.
- **Validates itself.** A no-lookahead, real-NSE-cost backtest lab with in-sample /
  out-of-sample discipline — every rule change is proven OOS before it ships to the paper
  book (see [Results](#results-honest-numbers) below).
- **Reports on itself.** A daily EOD report tracks P&L, win rate, profit factor, drawdown,
  benchmark-vs-NIFTY alpha, and a go-live gate scorecard — plus a Claude-written desk note.

## Architecture

```
Agent 1  Technical     pure Python: multi-factor score, Kestner trend confirmations,
                       ADX regime routing, hybrid exits (bank-half + chandelier trail)
Agent 2  Fundamental   news sentiment, kill-words, earnings-window veto, quality score
Agent 3  Strategist    ONE Claude call, strict JSON, EV-first — can only veto/downgrade,
                       never invent a number or upgrade a sub-threshold signal
Agent 4  Executor      paper portfolio, Kelly-aware sizing, kill switches, defined-risk
                       options via live Angel One chains, full decision audit trail
Agent 5  Reporter      daily P&L / gates / benchmark report + Claude EOD narrative
Agent 6  News Brain    pre-market 24h news → sector-contagion briefing → strategist veto
```

Supporting layers: `universe.py` (NSE index constituents, free), `screener_engine.py`
(whole-universe ranked scan), a React/lightweight-charts single-file cockpit UI, and
`backtest_lab.py` / `ml_signal.py` (the validation + ML layer).

## Results (honest numbers)

Backtests use real NSE transaction costs, no lookahead (signal at close → fill at next
open + tier slippage), and an in-sample (2019–23) / out-of-sample (2024–26) split — rules
are tuned on IS, judged only on OOS. Full-period numbers are always better than OOS; **OOS
is the number that matters**.

| Config | OOS trades | OOS win% | OOS PF | OOS expectancy | OOS Sharpe |
|---|---|---|---|---|---|
| Curated watchlist, V10.1 | 94 | 57.4% | **2.02** | ₹809/trade | 4.12 |
| Full Nifty-210 universe, V10.1 | 96 | 52.1% | **1.56** | ₹627/trade | 2.33 |

A cross-sectional ML layer (LightGBM, purged walk-forward CV) found **no signal** on 55
names (mean rank-IC −0.011) and was correctly gated off; the identical pipeline on ~200
names clears the noise gate (mean rank-IC +0.032–0.037) and is now live as a veto/tilt
signal only. One candidate config (tighter stop) looked excellent in-sample (PF 3.57) and
collapsed out-of-sample (PF 1.01) — a caught curve-fit, not shipped. Full methodology and
every experiment's numbers: `Algo Trading/SKILL.md` §5b.

**Caveats, stated plainly:** OOS samples are ~90–150 trades (real, but not yet the ≥50-trade,
multi-regime bar the system sets for itself before considering real capital); universe
backtests carry survivorship bias (today's index constituents applied to their own past);
this is not a claim of future performance.

## Setup

```bash
git clone https://github.com/RajDiptanshu/trading_bot.git
cd trading_bot
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env           # then fill in your own keys — see below
cd "Algo Trading/recommender"
pip install -r requirements.txt
python app.py                  # cockpit UI at http://127.0.0.1:8650
```

You'll need your own:
- **Anthropic API key** (Claude) — https://console.anthropic.com
- **Angel One SmartAPI credentials** — https://smartapi.angelbroking.com (free with an
  Angel One trading/demat account; used for live quotes, historical candles, and the
  option chain — no real orders are ever placed)
- (Optional) a Telegram bot token for kill-switch alerts

Scheduled automation (Windows Task Scheduler) is set up via the `setup_*.ps1` scripts —
run each once as Administrator. See `Algo Trading/SKILL.md` §5 for the full schedule.

## Reference / bibliography

The ML layer follows the approach in Stefan Jansen's
[*Machine Learning for Trading*](https://github.com/stefan-jansen/machine-learning-for-trading)
(cross-sectional rank features, purged walk-forward CV, information coefficient — not
vendored here, just followed). The (currently unused, deferred) ML forecasting model
[Kronos](https://github.com/shiyu-coder/Kronos) was evaluated as a future direction but
needs a separate Python 3.11 CPU environment (this project runs Python 3.14, no GPU) and
is not wired in. Strategy design also draws on Kestner's *Quantitative Trading Strategies*,
Sinclair's *Option Trading*, and Cohen's *The Bible of Options Strategies* (not
redistributed here — see your own copies).

## Status & disclaimer

**Paper trading only.** Every trade in this repo is simulated against a virtual ₹20L
book — no real orders are placed, and the code has no path to place one without an
explicit, deliberate change. Nothing here is financial advice. NSE/SEBI algorithmic
trading rules evolve — re-verify current regulations before ever considering real capital.
Go-live gates (documented in `Algo Trading/SKILL.md` §9) require months of paper evidence
across multiple market regimes before that conversation even starts.
