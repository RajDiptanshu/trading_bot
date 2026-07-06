"""
EXECUTION ENGINE — Angel One SmartAPI
=======================================
Runs at 9:16 AM daily via Task Scheduler.
Completes in under 60 seconds.

WHY THIS FILE EXISTS:
  morning_crew.py runs at 9:00 AM using yesterday's closing prices.
  NSE opens at 9:15 AM with overnight gap-up or gap-down.
  
  Example: COALINDIA closed at Rs 466. Signal generated at Rs 466.
           NSE opens at Rs 471 (gap up). Entering at Rs 471 changes
           the entire risk/reward ratio.
           
  This script:
    1. Reads pending_orders.json (what morning_crew approved)
    2. Fetches ACTUAL NSE opening prices via Angel One API
    3. Checks if opening price is within acceptable threshold (+-1.5%)
    4. If within threshold: logs trade at REAL price
    5. If price gapped too far: skips trade, logs reason
    6. Writes confirmed trades to paper_trades.json

NO LLM. NO AI. Pure price comparison math.
Angel One API gives real-time NSE prices with millisecond accuracy.
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
import json
import time
import pyotp
from datetime import datetime, date
from pathlib import Path

from SmartApi import SmartConnect
from dotenv import load_dotenv
from watchlist_reader import load_watchlist

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE_DIR       = Path(__file__).parent
PENDING_FILE   = BASE_DIR / "pending_orders.json"
TRADES_FILE    = BASE_DIR / "paper_trades.json"
MEMORY_FILE    = BASE_DIR / "strategy_memory.json"
LOG_FILE       = BASE_DIR / "decision_log.json"

# Load watchlist once
_, _, ANGEL_TOKENS, _, LIQUIDITY_TIERS = load_watchlist()

SLIPPAGE_PCT = {'NIFTY50': 0.001, 'MIDCAP': 0.002, 'SMALLCAP': 0.004}


def apply_entry_slippage(symbol: str, price: float, direction: str) -> float:
    tier = LIQUIDITY_TIERS.get(symbol, 'MIDCAP')
    pct  = SLIPPAGE_PCT.get(tier, 0.002)
    if direction == 'LONG':
        slipped = round(price * (1 + pct), 2)
    else:
        slipped = round(price * (1 - pct), 2)
    return slipped

# Max price deviation allowed from signal price
ENTRY_THRESHOLD_PCT = 1.5   # 1.5% — tight enough to avoid chasing gaps

# ══════════════════════════════════════════════════════════════
# FILE I/O
# ══════════════════════════════════════════════════════════════

def load_pending() -> list:
    """Reads pending_orders.json written by morning_crew.py"""
    if not PENDING_FILE.exists():
        return []
    with open(PENDING_FILE, encoding="utf-8") as f:
        return json.load(f)

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

def archive_pending():
    """Moves pending_orders.json to pending_orders_YYYY-MM-DD.json after processing"""
    if PENDING_FILE.exists():
        today = date.today().isoformat()
        archive = BASE_DIR / f"pending_orders_{today}.json"
        PENDING_FILE.rename(archive)

def reconcile_state(obj) -> list:
    local_open = [t.get('symbol') for t in load_trades() if t.get('status') == 'OPEN']
    if not local_open:
        return []
    try:
        book = obj.orderBook()
        exchange_open = [
            o.get('tradingsymbol', '').replace('-EQ', '')
            for o in (book.get('data') or [])
            if o.get('orderstatus', '').upper() in ('OPEN', 'PENDING', 'TRIGGER PENDING')
        ]
    except Exception as e:
        print(f'  WARNING: Could not fetch order book for reconciliation: {e}')
        return []
    orphaned = [s for s in exchange_open if s not in local_open]
    missing  = [s for s in local_open   if s not in exchange_open]
    if orphaned:
        print(f'  ⚠️  ORPHANED on exchange not in local records: {orphaned}')
        print(f'      Review manually before proceeding.')
    if missing:
        print(f'  ⚠️  LOCAL OPEN not found on exchange: {missing}')
        print(f'      May be filled, expired, or rejected.')
    return orphaned

# ══════════════════════════════════════════════════════════════
# ANGEL ONE CONNECTION
# ══════════════════════════════════════════════════════════════

def connect_angel() -> SmartConnect:
    """
    Connects to Angel One SmartAPI.
    Generates fresh TOTP automatically — no manual input needed.
    """
    api_key     = os.getenv("ANGEL_API_KEY")
    client_id   = os.getenv("ANGEL_CLIENT_ID")
    password    = os.getenv("ANGEL_PASSWORD")
    totp_secret = os.getenv("ANGEL_TOTP_SECRET")

    obj  = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    data = obj.generateSession(client_id, password, totp)

    if not data.get("status"):
        raise ConnectionError(f"Angel One login failed: {data.get('message')}")

    return obj

# ══════════════════════════════════════════════════════════════
# LIVE PRICE FETCH
# ══════════════════════════════════════════════════════════════

def get_live_price(obj: SmartConnect, symbol: str) -> float:
    """
    Fetches live NSE price via Angel One SmartAPI.
    Uses LTP (Last Traded Price) endpoint.
    Real-time, millisecond accuracy. No delay.
    """
    token = ANGEL_TOKENS.get(symbol)
    if not token:
        raise ValueError(f"No Angel token found for {symbol}")

    response = obj.ltpData("NSE", f"{symbol}-EQ", token)

    if response.get("status") and response.get("data"):
        ltp = response["data"].get("ltp")
        if ltp:
            return float(ltp)

    raise ValueError(f"Could not fetch LTP for {symbol}: {response}")


def poll_order_fill(obj, order_id: str, timeout_secs: int = 30) -> dict:
    import time as _time
    start = _time.time()
    while _time.time() - start < timeout_secs:
        try:
            book = obj.orderBook()
            if book.get('data'):
                for o in book['data']:
                    if str(o.get('orderid')) == str(order_id):
                        status = o.get('orderstatus', '').upper()
                        if status == 'COMPLETE':
                            return {
                                'filled':     True,
                                'fill_price': float(o.get('averageprice', 0)),
                                'fill_qty':   int(o.get('filledshares', 0)),
                                'order_id':   order_id,
                            }
                        elif status in ('REJECTED', 'CANCELLED'):
                            return {'filled': False, 'reason': status, 'order_id': order_id}
        except Exception as e:
            print(f'  Poll error: {e}')
        _time.sleep(2)
    try:
        obj.cancelOrder(order_id, 'NORMAL')
        print(f'  Order {order_id} timed out after {timeout_secs}s — cancelled')
    except Exception:
        pass
    return {'filled': False, 'reason': 'TIMEOUT', 'order_id': order_id}


def get_all_live_prices(obj: SmartConnect, symbols: list) -> dict:
    """
    Fetches live prices for all symbols.
    Retries once on failure. Returns None for failed fetches.
    """
    prices = {}
    for sym in symbols:
        try:
            prices[sym] = get_live_price(obj, sym)
            time.sleep(0.1)   # gentle rate limiting — 10 req/sec max
        except Exception as e:
            print(f"  WARNING: Price fetch failed for {sym}: {e}")
            # Retry once
            try:
                time.sleep(0.5)
                prices[sym] = get_live_price(obj, sym)
            except Exception:
                prices[sym] = None
    return prices

# ══════════════════════════════════════════════════════════════
# ENTRY THRESHOLD CHECK
# ══════════════════════════════════════════════════════════════

def check_entry_threshold(
    symbol: str,
    signal_price: float,
    live_price: float,
    direction: str
) -> tuple:
    """
    Checks if the live opening price is within acceptable range
    of the signal price generated at 9:00 AM.

    Rules:
      LONG trades: live price must not be more than 1.5% ABOVE signal
                   (gap up means we'd be chasing — skip)
                   live price below signal is fine (better entry)

      SHORT trades: live price must not be more than 1.5% BELOW signal
                    (gap down means we'd be chasing — skip)
                    live price above signal is fine (better entry)

    Returns: (is_valid, reason, adjusted_entry_price)
    """
    pct_diff = ((live_price - signal_price) / signal_price) * 100

    if direction == "LONG":
        if pct_diff > ENTRY_THRESHOLD_PCT:
            return (
                False,
                f"Gap up {pct_diff:+.1f}% — chasing entry. Signal: Rs{signal_price}, Open: Rs{live_price}",
                None
            )
        elif pct_diff < -5.0:
            return (
                False,
                f"Gap down {pct_diff:+.1f}% — possible bad news. Signal: Rs{signal_price}, Open: Rs{live_price}",
                None
            )
        else:
            reason = f"Entry confirmed at Rs{live_price} ({pct_diff:+.1f}% vs signal Rs{signal_price})"
            return True, reason, live_price

    elif direction == "SHORT":
        if pct_diff < -ENTRY_THRESHOLD_PCT:
            return (
                False,
                f"Gap down {pct_diff:+.1f}% — chasing short entry. Signal: Rs{signal_price}, Open: Rs{live_price}",
                None
            )
        elif pct_diff > 5.0:
            return (
                False,
                f"Gap up {pct_diff:+.1f}% on short — possible bullish news. Skip.",
                None
            )
        else:
            reason = f"Short entry confirmed at Rs{live_price} ({pct_diff:+.1f}% vs signal Rs{signal_price})"
            return True, reason, live_price

    return True, "Direction unknown — entering at live price", live_price


# ══════════════════════════════════════════════════════════════
# RECALCULATE SL AND TARGET AT ACTUAL ENTRY PRICE
# ══════════════════════════════════════════════════════════════

def recalculate_levels(order: dict, actual_entry: float) -> dict:
    """
    If entry price changed due to gap, recalculate SL and target
    using the same ATR distance but from the new entry price.

    This preserves the original ATR-based risk/reward structure
    even when price opens differently from the signal.
    """
    original_entry = order["entry_price"]
    original_sl    = order["stop_loss"]
    original_tgt   = order["target"]
    direction      = order["direction"]

    # Calculate original ATR distances
    sl_distance  = abs(original_entry - original_sl)
    tgt_distance = abs(original_tgt - original_entry)

    # Apply same distances from actual entry
    if direction == "LONG":
        new_sl  = round(actual_entry - sl_distance, 2)
        new_tgt = round(actual_entry + tgt_distance, 2)
    else:
        new_sl  = round(actual_entry + sl_distance, 2)
        new_tgt = round(actual_entry - tgt_distance, 2)

    updated = order.copy()
    updated["entry_price"]   = actual_entry
    updated["stop_loss"]     = new_sl
    updated["target"]        = new_tgt
    updated["signal_price"]  = original_entry   # keep original for reference
    updated["price_gap_pct"] = round(
        (actual_entry - original_entry) / original_entry * 100, 2
    )

    return updated


# ══════════════════════════════════════════════════════════════
# DECISION LOG
# ══════════════════════════════════════════════════════════════

def append_execution_log(confirmed: list, skipped: list, prices: dict):
    log_data = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            log_data = []

    log_data.append({
        "date":            date.today().isoformat(),
        "time":            datetime.now().strftime("%H:%M"),
        "type":            "EXECUTION_ENGINE",
        "confirmed_trades": len(confirmed),
        "skipped_trades":   len(skipped),
        "live_prices":      prices,
        "confirmed":        confirmed,
        "skipped":          skipped,
    })

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)


def poll_order_status(obj: SmartConnect, symbol: str, timeout: int = 30) -> bool:
    """
    Polls Angel One for 30s until the order for symbol is FILLED.
    Returns True if FILLED, False if CANCELLED or TIMEOUT.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Note: This is a placeholder for actual order status polling logic
            # In a real implementation, you'd use obj.orderBook() and filter by symbol
            response = obj.orderBook()
            if response.get("status") and response.get("data"):
                for order in response["data"]:
                    if order.get("symbol") == f"{symbol}-EQ" and order.get("status") == "complete":
                        return True
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return False

def reconcile_with_broker(obj: SmartConnect, paper_trades: list, is_paper: bool = True) -> list:
    """
    Reconciles paper_trades.json with the actual broker state.
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
                    print(f"  ⚠️  Reconciliation ALERT: {sym} in paper_trades but NOT in broker. Marking CANCELLED.")
                    trade["status"] = "CANCELLED_BY_BROKER"
                    trade["exit_date"] = date.today().isoformat()
                    trade["result"] = "RECONCILIATION_CANCEL"

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
    today      = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  EXECUTION ENGINE - {today} 9:16 AM")
    print(f"  Angel One SmartAPI - Real NSE Opening Prices")
    print(f"{'='*60}\n")

    # Load memory to check mode
    memory = load_memory()
    is_paper = not memory.get("meta", {}).get("deployment_ready", False)

    # Load pending orders from morning_crew
    pending = load_pending()
    
    # Connect to Angel One
    print("  Connecting to Angel One SmartAPI...")
    try:
        obj = connect_angel()
        print("  Angel One connected\n")
        
        print('  Reconciling state with exchange...')
        orphans = reconcile_state(obj)
        if orphans:
            print(f'  ABORTING — resolve orphaned orders before running: {orphans}')
            return
            
        # Step 0: Reconciliation (Sync existing state before adding new trades)
        trades = load_trades()
        if trades:
            trades = reconcile_with_broker(obj, trades, is_paper)
            save_trades(trades)
            
    except Exception as e:
        print(f"  Angel One connection failed: {e}")
        obj = None

    if not pending:
        print("  No pending orders from morning_crew.py")
        archive_pending()
        return

    print(f"  Pending orders: {len(pending)}")
    for p in pending:
        print(f"    {p['symbol']:12} Signal price: Rs{p['entry_price']}"
              f"  Direction: {p['direction']}")
    print()

    # Fetch live opening prices
    symbols = [p["symbol"] for p in pending]

    if obj:
        print(f"  Fetching live NSE prices for {len(symbols)} stocks...")
        live_prices = get_all_live_prices(obj, symbols)
        print(f"  Live prices fetched\n")
    else:
        print("  Angel One connection failed.")
        print("  ABORTING all trades — never execute blind.")
        append_execution_log([], pending, {})
        archive_pending()
        return
    # Check each pending order
    print(f"  {'Symbol':<12} {'Signal':>8} {'Live':>8} {'Gap':>7} {'Status'}")
    print(f"  {'─'*60}")

    confirmed = []
    skipped   = []

    for order in pending:
        sym          = order["symbol"]
        signal_price = order["entry_price"]
        live_price   = live_prices.get(sym)
        direction    = order["direction"]

        if live_price is None:
            print(f"  {sym:<12} Rs{signal_price:>7} {'N/A':>8} {'?':>7} SKIP - price unavailable")
            skipped.append({
                "symbol": sym,
                "reason": "Live price unavailable",
                "signal_price": signal_price,
            })
            continue

        pct_gap = ((live_price - signal_price) / signal_price) * 100

        is_valid, reason, actual_entry = check_entry_threshold(
            sym, signal_price, live_price, direction
        )

        gap_icon = "+" if pct_gap >= 0 else ""
        status   = "CONFIRMED" if is_valid else "SKIPPED"
        icon     = "OK" if is_valid else "NO"

        print(f"  {sym:<12} Rs{signal_price:>7} Rs{live_price:>7}"
              f" {gap_icon}{pct_gap:>5.1f}%  {icon} {status}")
        print(f"  {'':12} {reason}")

        if is_valid:
            # Recalculate SL and target from actual entry price
            final_order = recalculate_levels(order, actual_entry)
            
            # Update the entry_price with slippage
            final_order['entry_price'] = apply_entry_slippage(sym, final_order['entry_price'], direction)
            final_order['slippage_applied_pct'] = SLIPPAGE_PCT.get(LIQUIDITY_TIERS.get(sym, 'MIDCAP'), 0.002) * 100

            final_order["execution_time"] = datetime.now().strftime("%H:%M:%S")
            final_order["data_source"]    = "angel_one_live" if obj else "signal_price_fallback"
            
            # LIVE ORDER EXECUTION (PLACEHOLDER)
            if not is_paper:
                print(f"  [LIVE] Placing order for {sym}...")
                # Here you'd call obj.placeOrder()
                # Then poll for status
                filled = poll_order_status(obj, sym, timeout=30)
                if not filled:
                    print(f"  ⚠️  Order for {sym} NOT filled within 30s. Skipping.")
                    skipped.append({"symbol": sym, "reason": "Order not filled by broker", "signal_price": signal_price})
                    continue

            confirmed.append(final_order)
        else:
            skipped.append({
                "symbol":       sym,
                "reason":       reason,
                "signal_price": signal_price,
                "live_price":   live_price,
                "gap_pct":      round(pct_gap, 2),
            })

    # Write confirmed trades to paper_trades.json
    if confirmed:
        print(f"\n  {'─'*60}")
        trades  = load_trades()
        # memory is already loaded above
        
        for order in confirmed:
            trades.append(order)
            print(f"  LOGGED: {order['symbol']:12}"
                  f" Entry Rs{order['entry_price']}"
                  f"  SL Rs{order['stop_loss']}"
                  f"  T Rs{order['target']}")
            if order.get("price_gap_pct", 0) != 0:
                print(f"  {'':12} Gap adjusted from"
                      f" Rs{order['signal_price']}"
                      f" ({order['price_gap_pct']:+.1f}%)")

        save_trades(trades)

        # Update performance counters
        memory["performance_live"]["total_trades"]   += len(confirmed)
        memory["performance_live"]["open_positions"] += len(confirmed)
        save_memory(memory)

        print(f"\n  {len(confirmed)} trade(s) confirmed and logged")

    if skipped:
        print(f"\n  {len(skipped)} trade(s) skipped due to gap")
        for s in skipped:
            print(f"    {s['symbol']}: {s['reason']}")

    # Log everything
    append_execution_log(confirmed, skipped, live_prices)

    # Archive pending orders
    archive_pending()

    # Summary
    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'='*60}")
    print(f"  EXECUTION ENGINE COMPLETE - {elapsed}s")
    print(f"  Confirmed: {len(confirmed)}  |  Skipped: {len(skipped)}")
    print(f"  Data source: {'Angel One live prices' if obj else 'fallback signal prices'}")
    print(f"  Position monitor starts at 9:30 AM")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("execution_engine", "START")
    try:
        main()
        record_heartbeat("execution_engine", "FINISH")
    except Exception:
        record_heartbeat("execution_engine", "ERROR")
        raise