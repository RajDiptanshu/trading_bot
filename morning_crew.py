"""
MORNING CREW — Hybrid Architecture
====================================
Runs at 9:00 AM daily via Task Scheduler.
Completes in ~4-5 minutes, finishes well before 9:15 AM open.

THINKING LAYER (AI handles language/context):
  Step 1:  Run morning_scan.py   → technical JSON     [pure Python, no AI]
  Step 2:  DuckDuckGo search     → news headlines     [free, no tokens]
  Step 2B: yfinance .news        → fundamental news   [free, no tokens]
  Step 3:  ONE Claude API call   → synthesize + rank  [~4,000 tokens = ₹1.35]

DOING LAYER (Python handles math, never AI):
  Step 4:  strict_risk_manager() → sizing, sectors    [pure Python]
  Step 5:  log_trades()          → paper_trades.json  [pure Python]
  Step 6:  write_decision_log()  → why each trade     [pure Python]

Token budget: ~4,000 input + ~400 output = ₹1.35/day = ₹30/month
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
import sys
import json
import subprocess
from datetime import datetime, date, timezone
from pathlib import Path

import anthropic
from watchlist_reader import load_watchlist
import yfinance as yf
from dotenv import load_dotenv

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "strategy_memory.json"
TRADES_FILE = BASE_DIR / "paper_trades.json"
REPORTS_DIR = BASE_DIR / "daily_reports"
LOG_FILE    = BASE_DIR / "decision_log.json"
SCAN_SCRIPT = BASE_DIR / "morning_scan.py"

REPORTS_DIR.mkdir(exist_ok=True)


def load_memory() -> dict:
    with open(MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_trades() -> list:
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_trades(trades: list):
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)


# ══════════════════════════════════════════════════════════════
# STEP 1 — Technical Scan (pure Python, no AI)
# ══════════════════════════════════════════════════════════════

def run_technical_scan() -> list:
    """
    Runs morning_scan.py as subprocess, reads the saved JSON.
    Returns list of stocks with score >= effective_min_score (9-11).
    Pure Python. No AI. No tokens.
    """
    today     = date.today().strftime("%Y-%m-%d")
    scan_file = REPORTS_DIR / f"scan_{today}.json"

    print("  [1/6] Running technical scan (morning_scan.py)...")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, str(SCAN_SCRIPT)],
        capture_output=True, text=True,
        cwd=str(BASE_DIR), encoding="utf-8",
        errors="replace", env=env
    )

    if result.returncode != 0:
        print(f"  WARNING: Scan error: {result.stderr[:200]}")
        return []

    if not scan_file.exists():
        print(f"  WARNING: Scan file not found: {scan_file}")
        return []

    with open(scan_file, encoding="utf-8") as f:
        data = json.load(f)

    signals = data.get("signals", [])
    print(f"  OK Technical scan complete: {len(signals)} signals received")
    return signals


# ══════════════════════════════════════════════════════════════
# STEP 2 — News Research via DuckDuckGo (free, no tokens)
# ══════════════════════════════════════════════════════════════

def run_news_research(symbols: list) -> dict:
    """
    Searches DuckDuckGo for ALL 42 watchlist stocks + India VIX.
    Returns dict of {symbol: [headline1, headline2]}
    Free. No tokens. ~60 seconds total.
    """
    print(f"  [2/6] Searching news (DuckDuckGo) for {len(symbols)} stocks...")

    news_data = {}
    ddgs = DDGS()

    import time as _time

    def ddg_search(query, retries=3):
        for attempt in range(retries):
            try:
                return list(DDGS().text(query, max_results=2))
            except Exception as e:
                if attempt < retries - 1:
                    _time.sleep(2 ** attempt)
                else:
                    return []
        return []

    # India VIX first
    vix_results = ddg_search("India VIX today NSE")
    news_data["INDIA_VIX"] = [r["title"] for r in vix_results] if vix_results else ["VIX unavailable — proceeding on technicals"]

    # All watchlist stocks
    for sym in symbols:
        results = ddg_search(f"{sym} NSE India stock news today")
        news_data[sym] = [r["title"] for r in results] if results else ["News unavailable — proceeding on technicals"]

    print(f"  OK DuckDuckGo news complete: {len(news_data)} stocks covered")
    return news_data


# ══════════════════════════════════════════════════════════════
# STEP 2B — Fundamental News via yfinance (free, timestamped)
# ══════════════════════════════════════════════════════════════

def fetch_fundamental_news(symbols: list) -> dict:
    """
    Fetches structured fundamental news from Yahoo Finance via yfinance.
    Only returns news from the LAST 24 HOURS.

    Covers: quarterly earnings, corporate actions, analyst ratings,
    SEBI orders (when newsworthy), block deals, M&A news.

    Zero tokens. Free. Timestamped per item.
    """
    print(f"  [2B] Fetching fundamental news (yfinance) for {len(symbols)} stocks...")

    fundamental = {}
    cutoff_ts   = datetime.now(timezone.utc).timestamp() - (24 * 3600)

    for sym in symbols:
        try:
            ticker     = yf.Ticker(sym + ".NS")
            news_items = ticker.news or []

            recent = []
            for item in news_items:
                pub_time = item.get("providerPublishTime", 0)
                if pub_time >= cutoff_ts:
                    title     = item.get("title", "")
                    publisher = item.get("publisher", "")
                    pub_dt    = datetime.fromtimestamp(pub_time).strftime("%H:%M")
                    if title:
                        recent.append(f"[{pub_dt} {publisher}] {title}")

            fundamental[sym] = recent if recent else []

        except Exception:
            fundamental[sym] = []

    with_news = sum(1 for v in fundamental.values() if v)
    print(f"  OK Fundamental news: {with_news}/{len(symbols)} stocks had news in last 24h")
    return fundamental


# ══════════════════════════════════════════════════════════════
# STEP 3 — Claude Synthesis (ONE API call)
# ══════════════════════════════════════════════════════════════

def synthesize_with_claude(
    signals: list,
    combined_news: dict,
    memory: dict,
    open_positions: list
) -> list:
    """
    THE ONLY AI CALL IN THE MORNING WORKFLOW.
    Target: ~4,000 input tokens, ~400 output tokens = Rs 1.35
    """
    print("  [3/6] Claude synthesizing signals + news (1 API call)...")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    memory_rules = {
        "min_score":            memory["entry_rules"]["minimum_score_to_trade"],
        "blacklist":            memory["stock_universe"]["blacklist"]["stocks"],
        "favorites":            memory["stock_universe"]["high_conviction_favorites"]["stocks"],
        "sector_limits":        memory["portfolio_rules"]["sector_limits"],
        "circuit_active":       memory["circuit_breakers"]["circuit_active"],
        "kill_on_bearish_news": memory["entry_rules"]["news_sentiment_override"]["kill_trade_if_bearish_news"],
        "earnings_skip_days":   memory["entry_rules"]["news_sentiment_override"]["earnings_within_days"],
        "news_kill_words":      memory["news_filters"]["always_skip_keywords"],
        "weekly_insights":      memory["weekly_changelog"][-1].get("insights", [])
                                if memory["weekly_changelog"] else [],
    }

    open_sectors = [
        memory["stock_universe"]["sector_map"].get(p["symbol"], "Other")
        for p in open_positions
        if p.get("status") == "OPEN"
    ]

    signal_summary = [
        {
            "sym":       s["symbol"],
            "score":     s["score"],
            "strategy":  s["strategy"],
            "direction": s["direction"],
            "rsi":       s["rsi"],
            "sector":    s.get("sector", "Other"),
            "atr_pct":   s.get("atr_pct", 0),
            "atr_sl":    s.get("atr_sl"),
            "atr_target": s.get("atr_target"),
            "price":     s["price"],
        }
        for s in signals
    ]

    # Only send news for stocks that have signals — keeps tokens lean
    relevant_news = {
        s["symbol"]: combined_news.get(s["symbol"], [])
        for s in signals
    }
    relevant_news["INDIA_VIX"] = combined_news.get("INDIA_VIX", [])

    system_prompt = (
        "You are a strict trading risk analyst for NSE Indian equities. "
        "Review technical signals + news, apply rules, output ONLY the top 3 "
        "approved trades as a JSON array. Be decisive. No text outside the JSON."
    )

    user_prompt = f"""TECHNICAL SIGNALS (scored >=9/14, buckets: Trend 3pts | RS+52W 4pts | Oscillator 1pt | Volume 3pts | Volatility 1pt | Regime 2pts):
{json.dumps(signal_summary, indent=2)}

NEWS (DuckDuckGo + Yahoo Finance last 24h):
{json.dumps(relevant_news, indent=2)}

RULES:
{json.dumps(memory_rules, indent=2)}

OPEN SECTORS ALREADY: {open_sectors}
DATE: {date.today().isoformat()}

OUTPUT: JSON array of max 3 approved trades. Schema:
[
  {{
    "symbol": "COALINDIA",
    "approved": true,
    "score": 7,
    "direction": "LONG",
    "strategy": "MOMENTUM",
    "sector": "Mining",
    "price": 466.15,
    "atr_sl": 441.47,
    "atr_target": 503.17,
    "news_sentiment": "NEUTRAL",
    "reasoning": "Score 7/9, RSI 54 in sweet spot, no earnings, sector clear"
  }}
]

Enforce strictly:
- Kill if in blacklist
- Kill if news has kill words: {memory_rules['news_kill_words']}
- Kill if earnings within {memory_rules['earnings_skip_days']} days
- Kill if sector at limit already
- Kill if circuit_active=true
- Prefer MEAN_REVERSION > MOMENTUM > SHORT
- Return ONLY the JSON array. Nothing else."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        approved = json.loads(raw)
        print(f"  OK Claude approved {len(approved)} trade(s)")

        usage    = response.usage
        cost_usd = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
        cost_inr = cost_usd * 85
        print(f"  Tokens: {usage.input_tokens} in / {usage.output_tokens} out"
              f" = Rs {cost_inr:.2f}")

        return approved if isinstance(approved, list) else []

    except json.JSONDecodeError as e:
        print(f"  WARNING: Claude JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"  WARNING: Claude API error: {e}")
        return []


# ══════════════════════════════════════════════════════════════
# STEP 4 — Strict Risk Manager (pure Python, NEVER AI)
# ══════════════════════════════════════════════════════════════

def strict_risk_manager(
    approved_trades: list,
    memory: dict,
    open_positions: list
) -> list:
    """
    PURE PYTHON MATH. No LLM. No hallucinations.
    Determines instrument, calculates lot size, enforces all limits.
    """
    print("  [4/6] Running risk engine (pure Python math)...")

    capital         = memory["capital_rules"]["total_capital"]
    max_risk        = memory["capital_rules"]["max_risk_per_trade_inr"]
    max_positions   = memory["capital_rules"]["max_simultaneous_positions"]
    sector_limits   = memory["portfolio_rules"]["sector_limits"]
    lot_sizes       = memory["stock_universe"]["lot_sizes"]
    sector_map      = memory["stock_universe"]["sector_map"]
    kelly_params    = memory["capital_rules"]["kelly_params"]
    decision_matrix = memory["instrument_selection"]["decision_matrix"]
    vix_thresholds  = memory["instrument_selection"]["india_vix_thresholds"]

    open_sectors = {}
    current_open = sum(1 for p in open_positions if p.get("status") == "OPEN")
    for p in open_positions:
        if p.get("status") == "OPEN":
            sec = sector_map.get(p["symbol"], "Other")
            open_sectors[sec] = open_sectors.get(sec, 0) + 1

    if kelly_params["enabled"] and kelly_params["current_win_rate"] > 0:
        wr             = kelly_params["current_win_rate"]
        ratio          = kelly_params["avg_win_inr"] / max(kelly_params["avg_loss_inr"], 1)
        kelly_f        = max((wr - (1 - wr) / ratio) / 2, 0.01)
        kelly_f        = min(kelly_f, 0.05)
        risk_per_trade = capital * kelly_f
    else:
        risk_per_trade = min(max_risk, capital * 0.04)

    today_scan_file = REPORTS_DIR / f'scan_{date.today().isoformat()}.json'
    live_vix = None
    if today_scan_file.exists():
        try:
            with open(today_scan_file, encoding='utf-8') as _f:
                _scan = json.load(_f)
            live_vix = _scan.get('india_vix')
        except Exception:
            pass

    vix_thresholds = memory.get('instrument_selection', {}).get('india_vix_thresholds', {})
    if live_vix:
        extreme_floor = vix_thresholds.get('extreme_vol_floor', 24.0)
        high_floor    = vix_thresholds.get('high_vol_floor', 18.0)
        if live_vix >= extreme_floor:
            print(f'  VIX {live_vix} >= {extreme_floor} (extreme) — halting all new entries today')
            return []
        elif live_vix >= high_floor:
            risk_per_trade = round(risk_per_trade * 0.50, 2)
            print(f'  VIX {live_vix} elevated — risk halved to Rs{risk_per_trade:,.0f} per trade')

    # [V10] BUG FIX: this block previously compared average stock ATR%
    # (~1.5-3) against INDIA VIX thresholds (12/18), so vol_regime was
    # always "low_vix". Use the actual VIX when available; otherwise fall
    # back to ATR% with ATR-appropriate cutoffs.
    avg_atr_pct = (
        sum(t.get("atr_pct", 2.0) or 2.0 for t in approved_trades)
        / max(len(approved_trades), 1)
    )
    if live_vix:
        if live_vix >= vix_thresholds.get("high_vol_floor", 18.0):
            vol_regime = "high_vix"
        elif live_vix <= vix_thresholds.get("low_vol_ceiling", 12.0):
            vol_regime = "low_vix"
        else:
            vol_regime = "normal_vix"
    else:
        vol_regime = ("low_vix" if avg_atr_pct < 1.5
                      else "high_vix" if avg_atr_pct > 3.5
                      else "normal_vix")

    final_orders = []

    for trade in approved_trades:
        # No hard position limit — capital availability is the only constraint

        # Enforce sector limits
        sym       = trade.get("symbol")
        score     = trade.get("score", 5)
        price     = trade.get("price", 0)
        atr_sl   = trade.get("atr_sl") or trade.get("stop_loss")
        atr_tgt  = trade.get("atr_target") or trade.get("target")

        # Fallback: read from today scan JSON if Claude omitted levels
        if not atr_sl or not atr_tgt:
            import glob
            from datetime import date as _date
            scan_files = glob.glob(str(REPORTS_DIR / f"scan_{_date.today().isoformat()}.json"))
            if scan_files:
                with open(scan_files[0], encoding="utf-8") as _f:
                    scan_data = json.load(_f)
                for stock in scan_data.get("signals", []) + scan_data.get("all_stocks", []):
                    if stock.get("symbol") == sym:
                        atr_sl  = atr_sl  or stock.get("atr_sl")
                        atr_tgt = atr_tgt or stock.get("atr_target")
                        break
        sector    = sector_map.get(sym, "Other")
        strategy  = trade.get("strategy", "MOMENTUM")
        direction = trade.get("direction", "LONG")

        sector_limit = sector_limits.get(sector, sector_limits.get("default", 1))
        if open_sectors.get(sector, 0) >= sector_limit:
            print(f"  SKIP {sym}: sector {sector} at limit ({sector_limit})")
            continue

        if not atr_sl or not atr_tgt or price <= 0:
            print(f"  SKIP {sym}: missing price/SL/target data")
            continue

        atr_per_share = abs(price - atr_sl)
        if atr_per_share <= 0:
            print(f"  SKIP {sym}: invalid ATR stop loss")
            continue

        if strategy == "MEAN_REVERSION":
            matrix_key = "mean_reversion_any"
        elif direction == "SHORT":
            matrix_key = "short_momentum_any"
        elif score >= 11:
            matrix_key = f"score_11_14_{vol_regime}"
        elif score >= 9:
            matrix_key = "score_9_10_any_vix"
        else:
            matrix_key = "score_below_9"

        instrument_rule = decision_matrix.get(matrix_key, {})
        instrument      = instrument_rule.get("instrument", "SKIP")

        if instrument == "SKIP":
            print(f"  SKIP {sym}: score {score} maps to SKIP in decision matrix")
            continue

        lot = lot_sizes.get(sym, 250)

        if instrument == "EQUITY":
            qty       = max(int(risk_per_trade / atr_per_share), 1)
            MAX_GAP_PCT      = 0.20
            MAX_GAP_LOSS_INR = capital * 0.02
            gap_risk_qty     = max(int(MAX_GAP_LOSS_INR / (price * MAX_GAP_PCT)), 1)
            qty_pre_gap      = qty
            qty              = min(qty, gap_risk_qty)
            if qty < qty_pre_gap:
                print(f'  {sym}: Gap-risk cap reduced qty {qty_pre_gap} → {qty} (worst-case 20% gap capped at Rs{MAX_GAP_LOSS_INR:,.0f})')
            max_alloc = instrument_rule.get("max_allocation_inr", 50000)
            if qty * price > max_alloc:
                qty = max(int(max_alloc / price), 1)
            cost     = round(qty * price, 2)
            max_loss = round(qty * atr_per_share, 2)

        elif instrument in ("ATM_CALL", "ATM_PUT"):
            max_lot_cost = instrument_rule.get("max_lot_cost_inr", 15000)
            est_premium  = round(price * 0.025, 2)
            lot_cost     = est_premium * lot
            if lot_cost > max_lot_cost:
                print(f"  NOTE {sym}: lot cost Rs{lot_cost:,.0f} > max. Using EQUITY.")
                instrument = "EQUITY"
                qty        = max(int(risk_per_trade / atr_per_share), 1)
                cost       = round(qty * price, 2)
                max_loss   = round(qty * atr_per_share, 2)
            else:
                qty      = 1
                cost     = round(lot_cost, 2)
                max_loss = cost

        elif instrument == "BULL_CALL_SPREAD":
            max_lot_cost    = instrument_rule.get("max_lot_cost_inr", 10000)
            est_spread_cost = round(price * 0.009, 2)
            if est_spread_cost * lot > max_lot_cost:
                est_spread_cost = max_lot_cost / lot
            qty      = 1
            cost     = round(est_spread_cost * lot, 2)
            max_loss = cost

        elif instrument == "IRON_CONDOR":
            est_credit = round(price * 0.006, 2)
            qty        = 1
            cost       = round(est_credit * lot, 2)
            max_loss   = round(cost * 2, 2)

        else:
            qty      = 1
            cost     = round(price * 0.025 * lot, 2)
            max_loss = cost

        total_in_trades = sum(
            p.get("position_value", 0)
            for p in open_positions
            if p.get("status") == "OPEN"
        )
        if total_in_trades + cost > capital * 0.80:
            print(f"  SKIP {sym}: would exceed 80% capital allocation")
            continue

        order = {
            "id":             len(open_positions) + len(final_orders) + 1,
            "date":           date.today().isoformat(),
            "time":           datetime.now().strftime("%H:%M"),
            "symbol":         sym,
            "direction":      direction,
            "strategy":       strategy,
            "instrument":     instrument,
            "score":          score,
            "sector":         sector,
            "entry_price":    price,
            "quantity":       qty,
            "lot_size":       lot if instrument != "EQUITY" else 1,
            "position_value": cost,
            "stop_loss":      atr_sl,
            "target":         atr_tgt,
            "max_risk_inr":   max_loss,
            "atm_strike":     round(price / 20) * 20 if instrument != "EQUITY" else None,
            "vol_regime":     vol_regime,
            "news_sentiment": trade.get("news_sentiment", "NEUTRAL"),
            "reasoning":      trade.get("reasoning", ""),
            "status":         "OPEN",
            "exit_price":     None,
            "exit_date":      None,
            "pnl":            None,
            "result":         None,
        }

        final_orders.append(order)
        open_sectors[sector] = open_sectors.get(sector, 0) + 1
        print(f"  OK {sym}: {instrument} qty={qty}"
              f" risk=Rs{max_loss:,.0f}"
              f" SL=Rs{atr_sl} T=Rs{atr_tgt}")

    print(f"  [4/6] Risk engine done: {len(final_orders)} orders generated")
    return final_orders


# ══════════════════════════════════════════════════════════════
# STEP 5 — Log Trades (pure Python)
# ══════════════════════════════════════════════════════════════

def log_trades(orders: list, memory: dict):
    print("  [5/6] Saving to pending_orders.json...")

    if not orders:
        print("  No orders to log today.")
        return

    pending_file = BASE_DIR / "pending_orders.json"
    with open(pending_file, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)

    print(f"  OK {len(orders)} order(s) saved to pending_orders.json")
    print(f"  Execution engine confirms at 9:16 AM with live Angel One prices")

# ══════════════════════════════════════════════════════════════
# STEP 6 — Decision Log (pure Python)
# ══════════════════════════════════════════════════════════════

def write_decision_log(
    all_signals: list,
    approved: list,
    final_orders: list
):
    print("  [6/6] Writing decision log...")

    placed_symbols = {o["symbol"] for o in final_orders}

    log_entry = {
        "date":               date.today().isoformat(),
        "time":               datetime.now().strftime("%H:%M"),
        "signals_received":   len(all_signals),
        "claude_approved":    len(approved),
        "orders_placed":      len(final_orders),
        "trades":             final_orders,
        "approved_by_claude": approved,
        "skipped": [
            {
                "symbol": s["symbol"],
                "score":  s["score"],
                "reason": next(
                    (a.get("reasoning", "rejected by Claude")
                     for a in approved if a["symbol"] == s["symbol"]),
                    "not in Claude approval list"
                )
            }
            for s in all_signals
            if s["symbol"] not in placed_symbols
            and s["symbol"] not in {a["symbol"] for a in approved}
        ],
    }

    log_data = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            log_data = []

    log_data.append(log_entry)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)

    print(f"  OK Decision log saved to {LOG_FILE.name}")


# ══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER CHECK
# ══════════════════════════════════════════════════════════════

def check_circuit_breaker(memory: dict) -> bool:
    cb = memory["circuit_breakers"]
    if not cb["circuit_active"]:
        return False

    if cb.get("circuit_resets_on"):
        reset_date = date.fromisoformat(cb["circuit_resets_on"])
        if date.today() >= reset_date:
            memory["circuit_breakers"]["circuit_active"]     = False
            memory["circuit_breakers"]["circuit_tripped_on"] = None
            memory["circuit_breakers"]["circuit_resets_on"]  = None
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(memory, f, indent=2)
            print("  OK Circuit breaker reset. Trading resumed.")
            return False

    print(f"  CIRCUIT BREAKER ACTIVE - no trades today")
    print(f"  Resets on: {cb.get('circuit_resets_on', 'unknown')}")
    return True


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    start_time = datetime.now()
    today      = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  MORNING CREW - {today}")
    print(f"  Hybrid Architecture: AI thinks, Python executes")
    print(f"{'='*60}\n")

    memory         = load_memory()
    open_positions = [t for t in load_trades() if t.get("status") == "OPEN"]

    print(f"  Capital : Rs{memory['capital_rules']['total_capital']:,}")
    print(f"  Open    : {len(open_positions)} position(s)")
    print(f"  Target  : Rs{memory['performance_live']['month_target_inr']:,}"
          f" ({memory['performance_live']['month_target_pct']}%)")
    print()

    if check_circuit_breaker(memory):
        return

    # STEP 1: Technical scan
    signals = run_technical_scan()
    if not signals:
        print("  No signals today. System says WAIT.")
        write_decision_log([], [], [])
        return

    # Load watchlist dynamically
    all_symbols, sector_map, _, lot_sizes, _ = load_watchlist()

    # Update memory with fresh watchlist data to ensure consistency
    memory["stock_universe"]["active_watchlist"] = all_symbols
    memory["stock_universe"]["sector_map"]      = sector_map
    memory["stock_universe"]["lot_sizes"]       = lot_sizes

    # STEP 2: DuckDuckGo news for all 42 stocks
    news        = run_news_research(all_symbols)

    # STEP 2B: Fundamental news via yfinance
    fundamental = fetch_fundamental_news(all_symbols)

    # Merge both news sources per symbol
    combined_news = {}
    for sym in all_symbols:
        combined_news[sym] = news.get(sym, []) + fundamental.get(sym, [])
    combined_news["INDIA_VIX"] = news.get("INDIA_VIX", [])

    # STEP 3: Claude synthesis (1 API call)
    approved = synthesize_with_claude(signals, combined_news, memory, open_positions)
    if not approved:
        print("  Claude approved 0 trades. Check decision_log.json for reasons.")
        write_decision_log(signals, [], [])
        return

    # STEP 4: Risk engine (pure Python math)
    final_orders = strict_risk_manager(approved, memory, open_positions)

    # STEP 5: Log trades
    log_trades(final_orders, memory)

    # STEP 6: Decision log
    write_decision_log(signals, approved, final_orders)

    # Summary
    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'='*60}")
    print(f"  MORNING CREW COMPLETE - {elapsed}s")
    print(f"{'='*60}")
    print(f"  Signals scanned  : {len(signals)}")
    print(f"  Claude approved  : {len(approved)}")
    print(f"  Orders placed    : {len(final_orders)}")
    print()
    for o in final_orders:
        print(f"  {o['symbol']:12} {o['instrument']:20}"
              f" Rs{o['entry_price']:>8}"
              f"  SL Rs{o['stop_loss']}  T Rs{o['target']}")
    print(f"\n  Sentinel runs at 11:00 AM")
    print(f"  Position monitor runs every 30 mins from 9:30 AM to 3:15 PM")
    print(f"  Check decision_log.json for full reasoning")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("morning_crew", "START")
    try:
        main()
        record_heartbeat("morning_crew", "FINISH")
    except Exception:
        record_heartbeat("morning_crew", "ERROR")
        raise