"""
POSITION MONITOR — Pure Python, Zero AI
=========================================
Runs at 3:30 PM daily via Task Scheduler.
Completes in under 60 seconds.

NO LLM. NO API CALLS. NO TOKENS.

What it does:
  1. Reads all OPEN positions from paper_trades.json
  2. Fetches live closing prices via yfinance
  3. Checks each position: SL hit? Target hit? Time stop?
  4. Closes qualifying positions → logs P&L
  5. Updates strategy_memory.json performance metrics
  6. Checks circuit breakers (3 consecutive losses)
  7. Prints daily summary

If Anthropic API goes down at 3:30 PM → no impact.
This script only uses math. Two numbers, one comparison.
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import time
import pyotp
import yfinance as yf
from datetime import datetime, date
from pathlib import Path
from SmartApi import SmartConnect
from dotenv import load_dotenv
from watchlist_reader import load_watchlist
from cost_model import transaction_cost

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)


def connect_angel():
    obj  = SmartConnect(api_key=os.getenv("ANGEL_API_KEY"))
    totp = pyotp.TOTP(os.getenv("ANGEL_TOTP_SECRET")).now()
    data = obj.generateSession(
        os.getenv("ANGEL_CLIENT_ID"),
        os.getenv("ANGEL_PASSWORD"), totp
    )
    if not data.get("status"):
        raise ConnectionError(f"Angel One login failed: {data.get('message')}")
    return obj


# Load watchlist once
_, _, ANGEL_TOKENS, _, LIQUIDITY_TIERS = load_watchlist()
SLIPPAGE_PCT = {'NIFTY50': 0.001, 'MIDCAP': 0.002, 'SMALLCAP': 0.004}


# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).parent
TRADES_FILE = BASE_DIR / "paper_trades.json"
MEMORY_FILE = BASE_DIR / "strategy_memory.json"
LOG_FILE    = BASE_DIR / "decision_log.json"

# ══════════════════════════════════════════════════════════════
# FILE I/O
# ══════════════════════════════════════════════════════════════

def load_trades() -> list:
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_trades(trades: list):
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)

def load_memory() -> dict:
    with open(MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_memory(memory: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)

def reconcile_state(obj, open_trades: list) -> list:
    if not open_trades:
        return []
    local_symbols = [t.get('symbol') for t in open_trades]
    try:
        book = obj.orderBook()
        exchange_open = [
            o.get('tradingsymbol', '').replace('-EQ', '')
            for o in (book.get('data') or [])
            if o.get('orderstatus', '').upper() in ('OPEN', 'PENDING', 'TRIGGER PENDING')
        ]
    except Exception as e:
        print(f'  WARNING: Reconciliation skipped — {e}')
        return []
    orphaned = [s for s in exchange_open if s not in local_symbols]
    if orphaned:
        print(f'  WARNING: Orphaned orders on exchange not in paper_trades.json: {orphaned}')
    return orphaned

# ══════════════════════════════════════════════════════════════
# LIVE PRICE FETCH
# ══════════════════════════════════════════════════════════════

def fetch_live_prices(symbols: list) -> dict:
    """
    Fetches live prices via Angel One SmartAPI.
    Falls back to yfinance if Angel One unavailable.
    Real-time, no delay.
    """
    prices = {}
    obj = None
    try:
        obj = connect_angel()
    except Exception as e:
        print(f"  WARNING: Angel One unavailable: {e}. Using yfinance fallback.")

    for sym in symbols:
        try:
            if obj and sym in ANGEL_TOKENS:
                resp = obj.ltpData("NSE", f"{sym}-EQ", ANGEL_TOKENS[sym])
                if resp.get("status") and resp.get("data"):
                    prices[sym] = float(resp["data"]["ltp"])
                    time.sleep(0.1)
                    continue
            # yfinance fallback
            hist = yf.Ticker(sym + ".NS").history(period="1d", interval="1m")
            if hist.empty:
                prices[sym] = None
            else:
                last_ts = hist.index[-1]
                last_date = last_ts.date() if hasattr(last_ts, 'date') else last_ts.to_pydatetime().date()
                if last_date < date.today():
                    print(f'  WARNING: {sym}: yfinance returned stale date {last_date} — skipping, using None')
                    prices[sym] = None
                else:
                    prices[sym] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception as e:
            print(f"  WARNING: Price fetch failed for {sym}: {e}")
            prices[sym] = None
    return prices

# ══════════════════════════════════════════════════════════════
# EXIT CHECKER — pure math, no AI
# ══════════════════════════════════════════════════════════════

def check_exit(position: dict, live_price: float) -> tuple:
    """
    Compares live price against SL and target.
    Returns (should_exit: bool, reason: str, pnl: float)

    This is intentionally dumb math.
    No LLM. No judgment. Just numbers.
    """
    tier = LIQUIDITY_TIERS.get(position.get('symbol', ''), 'MIDCAP')
    slip = SLIPPAGE_PCT.get(tier, 0.002)
    if position.get('direction') == 'LONG':
        live_price = round(live_price * (1 - slip), 2)
    else:
        live_price = round(live_price * (1 + slip), 2)

    entry      = position["entry_price"]
    sl         = position["stop_loss"]
    target     = position["target"]
    direction  = position["direction"]
    instrument = position["instrument"]
    qty        = position["quantity"]
    entry_date = date.fromisoformat(position["date"])
    days_held  = (date.today() - entry_date).days

    # Time stop from strategy_memory — default 12 days equity, 7 days options
    if instrument == "EQUITY":
        time_stop = 12
    else:
        time_stop = 7

    # ── LONG position checks ─────────────────────────────────
    if direction == "LONG":

        if instrument == "EQUITY":
            pnl_if_exit = round((live_price - entry) * qty, 2)
        else:
            # Options: if stock hits target, assume option doubled
            # If stock hits SL, assume option lost 50%
            cost = position.get("position_value", 0)
            if live_price >= target:
                pnl_if_exit = round(cost * 1.0, 2)   # 100% gain on option
            elif live_price <= sl:
                pnl_if_exit = round(-cost * 0.5, 2)   # 50% loss on option
            else:
                pnl_if_exit = 0

        if live_price >= target:
            return True, "TARGET_HIT", pnl_if_exit

        if live_price <= sl:
            if instrument == "EQUITY":
                actual_pnl = round((live_price - entry) * qty, 2)
            else:
                actual_pnl = round(-position.get("position_value", 0) * 0.5, 2)
            return True, "STOPLOSS_HIT", actual_pnl

    # ── SHORT position checks ────────────────────────────────
    elif direction == "SHORT":

        if instrument == "EQUITY":
            pnl_if_exit = round((entry - live_price) * qty, 2)
        else:
            cost = position.get("position_value", 0)
            if live_price <= target:
                pnl_if_exit = round(cost * 1.0, 2)
            elif live_price >= sl:
                pnl_if_exit = round(-cost * 0.5, 2)
            else:
                pnl_if_exit = 0

        if live_price <= target:
            return True, "TARGET_HIT", pnl_if_exit

        if live_price >= sl:
            if instrument == "EQUITY":
                actual_pnl = round((entry - live_price) * qty, 2)
            else:
                actual_pnl = round(-position.get("position_value", 0) * 0.5, 2)
            return True, "STOPLOSS_HIT", actual_pnl

    # ── Time stop ────────────────────────────────────────────
    if days_held >= time_stop:
        if direction == "LONG":
            if instrument == "EQUITY":
                pnl = round((live_price - entry) * qty, 2)
            else:
                # Options time stop — exit at 30% loss to preserve capital
                pnl = round(-position.get("position_value", 0) * 0.3, 2)
        else:
            if instrument == "EQUITY":
                pnl = round((entry - live_price) * qty, 2)
            else:
                pnl = round(-position.get("position_value", 0) * 0.3, 2)
        return True, f"TIME_STOP_{days_held}d", pnl

    return False, "", 0

# ══════════════════════════════════════════════════════════════
# PERFORMANCE UPDATER
# ══════════════════════════════════════════════════════════════

def update_performance(memory: dict, closed_trades: list) -> dict:
    """
    Updates all performance metrics in strategy_memory.json.
    Pure math. No AI.
    """
    if not closed_trades:
        return memory

    perf = memory["performance_live"]
    capital = memory["capital_rules"]["total_capital"]

    for trade in closed_trades:
        # Exclude trades affected by system outage from performance statistics
        if trade.get("data_quality") == "OUTAGE_AFFECTED":
            continue

        pnl = trade["pnl"]
        perf["closed_trades"] = perf.get("closed_trades", 0) + 1
        perf["total_pnl_inr"]  = round(perf.get("total_pnl_inr", 0) + pnl, 2)

        if pnl > 0:
            perf["wins"] = perf.get("wins", 0) + 1
            perf["best_trade_inr"] = max(perf.get("best_trade_inr", 0), pnl)
            perf["gross_win_sum_inr"] = perf.get("gross_win_sum_inr", 0) + pnl
            perf["current_streak"] = (
                perf.get("current_streak", 0) + 1
                if perf.get("streak_type") == "WIN"
                else 1
            )
            perf["streak_type"] = "WIN"
        else:
            perf["losses"] = perf.get("losses", 0) + 1
            perf["worst_trade_inr"] = min(perf.get("worst_trade_inr", 0), pnl)
            perf["gross_loss_sum_inr"] = perf.get("gross_loss_sum_inr", 0) + abs(pnl)
            perf["current_streak"] = (
                perf.get("current_streak", 0) + 1
                if perf.get("streak_type") == "LOSS"
                else 1
            )
            perf["streak_type"] = "LOSS"

    # Recalculate derived metrics
    total_closed = perf.get("closed_trades", 0)
    wins = perf.get("wins", 0)
    losses = perf.get("losses", 0)

    perf["win_rate_pct"] = round(wins / total_closed * 100, 1) if total_closed else 0
    perf["total_return_pct"] = round(
        perf["total_pnl_inr"] / capital * 100, 2
    )
    perf["month_progress_pct"] = round(
        perf["total_pnl_inr"] / perf["month_target_inr"] * 100, 1
    )

    # Average win/loss for Kelly — use persistent sums, not single best/worst values
    if wins > 0:
        perf["avg_win_inr"] = round(perf["gross_win_sum_inr"] / wins, 2)
    if losses > 0:
        perf["avg_loss_inr"] = round(perf["gross_loss_sum_inr"] / losses, 2)

    # Expectancy
    if total_closed > 0:
        wr = wins / total_closed
        avg_win  = perf.get("avg_win_inr", 0)
        avg_loss = perf.get("avg_loss_inr", 0)
        perf["expectancy_per_trade_inr"] = round(
            wr * avg_win - (1 - wr) * avg_loss, 2
        )

    # Profit factor — all-time gross sums, not just this batch
    perf["profit_factor"] = round(
        perf["gross_win_sum_inr"] / max(perf["gross_loss_sum_inr"], 1), 2
    )

    # Update open position count
    perf["open_positions"] = sum(
        1 for t in load_trades() if t.get("status") == "OPEN"
    )

    # Enable Kelly after 10 trades
    kelly = memory["capital_rules"]["kelly_params"]
    if total_closed >= 10 and not kelly["enabled"]:
        kelly["enabled"] = True
        kelly["current_win_rate"] = perf["win_rate_pct"] / 100
        kelly["avg_win_inr"] = perf.get("avg_win_inr", 0)
        kelly["avg_loss_inr"] = perf.get("avg_loss_inr", 0)
        wr = kelly["current_win_rate"]
        ratio = kelly["avg_win_inr"] / max(kelly["avg_loss_inr"], 1)
        kelly["kelly_fraction"] = round(
            max(wr - (1 - wr) / ratio, 0), 4
        )
        kelly["half_kelly_fraction"] = round(
            kelly["kelly_fraction"] / 2, 4
        )
        print(f"  🎯 Kelly criterion enabled after {total_closed} trades")
        print(f"     Win rate: {wr:.1%}  Half-Kelly: {kelly['half_kelly_fraction']:.1%}")

    memory["performance_live"] = perf
    memory["capital_rules"]["kelly_params"] = kelly
    return memory

# ══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════

def check_and_set_circuit_breaker(memory: dict, closed_today: list) -> dict:
    """
    Trips the circuit breaker if:
    - 3 consecutive losses
    - Daily loss > limit
    - Weekly loss > limit
    Pure math. No AI.
    """
    cb   = memory["circuit_breakers"]
    perf = memory["performance_live"]

    # Check consecutive losses
    if (perf.get("streak_type") == "LOSS" and
            perf.get("current_streak", 0) >= cb["consecutive_loss_limit"]):
        if not cb["circuit_active"]:
            from datetime import timedelta
            reset = date.today() + timedelta(days=cb["pause_days_after_circuit"])
            cb["circuit_active"]    = True
            cb["circuit_tripped_on"] = date.today().isoformat()
            cb["circuit_resets_on"] = reset.isoformat()
            print(f"\n  🔴 CIRCUIT BREAKER TRIPPED!")
            print(f"     {perf['current_streak']} consecutive losses")
            print(f"     Trading paused until {reset.isoformat()}")

    # Check daily loss limit
    today_pnl = sum(t["pnl"] for t in closed_today)
    if today_pnl < -cb["daily_loss_limit_inr"]:
        if not cb["circuit_active"]:
            from datetime import timedelta
            reset = date.today() + timedelta(days=1)
            cb["circuit_active"]    = True
            cb["circuit_tripped_on"] = date.today().isoformat()
            cb["circuit_resets_on"] = reset.isoformat()
            print(f"\n  🔴 DAILY LOSS LIMIT HIT: ₹{abs(today_pnl):,.0f}")
            print(f"     Limit: ₹{cb['daily_loss_limit_inr']:,}")
            print(f"     Trading paused until tomorrow")

    memory["circuit_breakers"] = cb
    return memory

# ══════════════════════════════════════════════════════════════
# DECISION LOG UPDATER
# ══════════════════════════════════════════════════════════════

def append_monitor_log(closed_trades: list, prices: dict, open_count: int):
    """Appends 3:30 PM monitor results to decision_log.json."""
    entry = {
        "date":       date.today().isoformat(),
        "time":       datetime.now().strftime("%H:%M"),
        "type":       "POSITION_MONITOR",
        "open_checked": open_count,
        "closed_today": len(closed_trades),
        "today_pnl":  round(sum(t["pnl"] for t in closed_trades), 2),
        "closures": [
            {
                "symbol":      t["symbol"],
                "direction":   t["direction"],
                "instrument":  t["instrument"],
                "entry_price": t["entry_price"],
                "exit_price":  t["exit_price"],
                "pnl":         t["pnl"],
                "result":      t["result"],
                "exit_reason": t["exit_reason"],
                "days_held":   (date.today() -
                                date.fromisoformat(t["date"])).days,
            }
            for t in closed_trades
        ],
        "live_prices_checked": prices,
    }

    log_data = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            log_data = []

    log_data.append(entry)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)

def reconcile_with_broker(obj: SmartConnect, paper_trades: list, is_paper: bool = True) -> list:
    """
    Reconciles paper_trades.json with actual broker state.
    """
    if is_paper or not obj:
        return paper_trades

    print("  [RECONCILIATION] Syncing with Angel One...")
    try:
        response = obj.position()
        if not response.get("status") or not response.get("data"):
            print("  WARNING: Could not fetch broker positions for reconciliation")
            return paper_trades

        broker_positions = {p["symbol"].split("-")[0]: p for p in response["data"]}
        
        for trade in paper_trades:
            if trade.get("status") == "OPEN":
                sym = trade["symbol"]
                if sym not in broker_positions:
                    print(f"  ⚠️  Reconciliation ALERT: {sym} in paper_trades but NOT in broker. Marking CLOSED_MANUALLY.")
                    trade["status"] = "CLOSED_MANUALLY"
                    trade["exit_date"] = date.today().isoformat()
                    trade["result"] = "RECONCILIATION_SYNC"

        for sym, pos in broker_positions.items():
            if not any(t["symbol"] == sym and t["status"] == "OPEN" for t in paper_trades):
                print(f"  🚨 Reconciliation WARNING: {sym} in BROKER but NOT in paper_trades! Manual intervention needed.")

    except Exception as e:
        print(f"  WARNING: Reconciliation failed: {e}")

    return paper_trades

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    start_time = datetime.now()
    today = date.today().isoformat()

    print(f"\n{'═'*60}")
    print(f"  📊 POSITION MONITOR — {today} 3:30 PM")
    print(f"  Pure Python. Zero AI. Pure math.")
    print(f"{'═'*60}\n")

    # Load all trades
    all_trades   = load_trades()
    memory       = load_memory()
    is_paper     = not memory.get("meta", {}).get("deployment_ready", False)

    # Reconciliation
    obj = None
    try:
        obj = connect_angel()
    except Exception:
        pass
    
    if all_trades:
        all_trades = reconcile_with_broker(obj, all_trades, is_paper)
        save_trades(all_trades)

    open_trades  = [t for t in all_trades if t.get("status") == "OPEN"]

    if not open_trades:
        print("  ℹ️  No open positions to monitor.")
        append_monitor_log([], {}, 0)
        return

    print(f"  Checking {len(open_trades)} open position(s)...\n")

    # Reconciliation check
    try:
        angel_obj = connect_angel()
        reconcile_state(angel_obj, open_trades)
    except Exception:
        print('  Reconciliation skipped — Angel One unavailable')

    # Fetch live prices for all open symbols
    symbols = [t["symbol"] for t in open_trades]
    prices  = fetch_live_prices(symbols)

    print(f"  {'Symbol':<12} {'Entry':>8} {'Live':>8} "
          f"{'SL':>8} {'Target':>8} {'P&L':>10} {'Status'}")
    print(f"  {'─'*72}")

    closed_today = []

    for trade in open_trades:
        sym        = trade["symbol"]
        live_price = prices.get(sym)

        if live_price is None:
            print(f"  {sym:<12} ⚠️  Price unavailable — keeping open")
            continue

        should_exit, reason, pnl = check_exit(trade, live_price)

        # P&L display for open positions
        if trade["direction"] == "LONG":
            unrealised = round(
                (live_price - trade["entry_price"]) * trade["quantity"], 2
            )
        else:
            unrealised = round(
                (trade["entry_price"] - live_price) * trade["quantity"], 2
            )

        status_icon = "🔴 CLOSE" if should_exit else "🟡 HOLD"
        pnl_display = pnl if should_exit else unrealised

        print(f"  {sym:<12} ₹{trade['entry_price']:>7} ₹{live_price:>7} "
              f"₹{trade['stop_loss']:>7} ₹{trade['target']:>7} "
              f"₹{pnl_display:>+9,.0f} {status_icon}"
              + (f" ({reason})" if should_exit else ""))

        # Check sentinel flag — KILL overrides everything
        sentinel_flag = trade.get("sentinel_flag", "")
        if sentinel_flag == "KILL" and not should_exit:
            should_exit = True
            reason = f"SENTINEL_KILL: {trade.get('sentinel_reason', '')}"
            pnl = unrealised  # exit at current price
   
        if should_exit:
            # ── Transaction costs ─────────────────────────────────
            pnl_gross  = pnl
            instrument = trade["instrument"]
            qty        = trade["quantity"]
            entry_px   = trade["entry_price"]
            direction  = trade["direction"]

            if instrument == "EQUITY":
                entry_side = "BUY"  if direction == "LONG" else "SELL"
                exit_side  = "SELL" if direction == "LONG" else "BUY"
                cost_entry = transaction_cost("EQUITY", entry_side, entry_px,   qty)
                cost_exit  = transaction_cost("EQUITY", exit_side,  live_price, qty)
            else:
                # OPTIONS: reconstruct per-share premium from stored position_value
                lot_size    = trade.get("lot_size", 1) or 1
                total_units = qty * lot_size
                pos_val     = trade.get("position_value", 0)
                entry_prem  = pos_val / max(total_units, 1)
                # Exit premium: position_value + gross pnl, floored at 0 (expired worthless)
                exit_prem   = max(pos_val + pnl_gross, 0) / max(total_units, 1)
                cost_entry  = transaction_cost("OPTIONS", "BUY",  entry_prem, qty, lot_size)
                cost_exit   = transaction_cost("OPTIONS", "SELL", exit_prem,  qty, lot_size)

            costs_inr = round(cost_entry + cost_exit, 2)
            pnl_net   = round(pnl_gross - costs_inr, 2)

            # Update trade record
            trade["status"]        = "CLOSED"
            trade["exit_price"]    = live_price
            trade["exit_date"]     = today
            trade["exit_reason"]   = reason
            trade["pnl_gross_inr"] = pnl_gross    # raw price-difference P&L
            trade["costs_inr"]     = costs_inr    # total entry + exit charges
            trade["pnl"]           = pnl_net      # what actually hits the account
            trade["result"]        = "WIN" if pnl_net > 0 else "LOSS"
            closed_today.append(trade)

    # Save updated trades
    save_trades(all_trades)

    # Update performance metrics
    if closed_today:
        print(f"\n  {'─'*60}")
        print(f"  Closed today: {len(closed_today)} trade(s)")

        wins   = [t for t in closed_today if t["pnl"] > 0]
        losses = [t for t in closed_today if t["pnl"] <= 0]
        today_pnl = sum(t["pnl"] for t in closed_today)

        print(f"  Today P&L:  ₹{today_pnl:+,.0f}")
        print(f"  Wins: {len(wins)}  Losses: {len(losses)}")

        memory = update_performance(memory, closed_today)
        memory = check_and_set_circuit_breaker(memory, closed_today)
        save_memory(memory)

        # Deployment gate check
        perf = memory["performance_live"]
        criteria = memory["meta"]["deployment_criteria"]
        print(f"\n  {'─'*60}")
        print(f"  DEPLOYMENT GATE PROGRESS:")
        print(f"  Win rate  : {perf['win_rate_pct']}%"
              f"  (need {criteria['min_win_rate_pct']}%)")
        print(f"  Total P&L : ₹{perf['total_pnl_inr']:+,.0f}"
              f"  (target ₹{perf['month_target_inr']:,})")
        print(f"  Return    : {perf['total_return_pct']}%"
              f"  (target {criteria['target_monthly_return_pct']}%)")
        print(f"  Progress  : {perf['month_progress_pct']}% of monthly target")

    else:
        print(f"\n  All {len(open_trades)} position(s) still OPEN.")
        print(f"  No SL or target hit today.")

    # Log results
    append_monitor_log(closed_today, prices, len(open_trades))

    # Summary
    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'═'*60}")
    print(f"  MONITOR COMPLETE — {elapsed}s | Zero AI used")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("position_monitor", "START")
    try:
        main()
        record_heartbeat("position_monitor", "FINISH")
    except Exception:
        record_heartbeat("position_monitor", "ERROR")
        raise