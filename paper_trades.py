import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import yfinance as yf
from datetime import datetime

# ════════════════════════════════════════════════════════════
#  PAPER TRADE JOURNAL
#  Log every signal your screener generates — BUY, WATCHING,
#  or CLOSED. This is your Phase 5 gate.
#  Must show positive expectancy over 4+ weeks before
#  deploying real capital.
# ════════════════════════════════════════════════════════════

LOG_FILE = "paper_trades.json"

# ════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ════════════════════════════════════════════════════════════

def log_trade(symbol, action, price, target, sl,
              reason, quantity=1):
    """
    Records a new paper trade entry.
    Call this whenever screener gives BUY or WATCHING signal.

    quantity : number of shares (use 1 for now, scale later)
    action   : "BUY", "WATCHING", or "SHORT"
    """
    trade = {
        "id":         _next_id(),
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "time":       datetime.now().strftime("%H:%M"),
        "symbol":     symbol,
        "action":     action,
        "entry":      price,
        "quantity":   quantity,
        "target":     target,
        "sl":         sl,
        "rr":         round((target - price) /
                            (price - sl), 2) if price != sl else 0,
        "reason":     reason,
        "status":     "WATCHING" if "WATCH" in action.upper()
                      else "OPEN",
        "exit_price": None,
        "exit_date":  None,
        "pnl":        None,
        "pnl_pct":    None,
        "result":     None,
    }

    trades = _load()
    trades.append(trade)
    _save(trades)

    print(f"\n✅ Paper trade logged: {action} {symbol} "
          f"@ ₹{price}")
    print(f"   Target : ₹{target}  |  SL : ₹{sl}  "
          f"|  RR : {trade['rr']}")
    print(f"   Qty    : {quantity} shares  "
          f"|  Status: {trade['status']}")


def close_trade(symbol, exit_price, reason="Target/SL hit"):
    """
    Closes the most recent open/watching trade for a symbol.
    Calculates P&L automatically.

    Call this when:
    - Price hits your target → profit
    - Price hits your SL    → loss
    - You manually exit     → specify reason
    """
    trades = _load()
    closed = False

    for i in range(len(trades) - 1, -1, -1):
        t = trades[i]
        if (t["symbol"] == symbol and
                t["status"] in ["OPEN", "WATCHING"]):

            qty  = t.get("quantity", 1)
            pnl  = round((exit_price - (t.get('entry') or t.get('entry_price', 0))) * qty, 2)
            pct  = round(
                ((exit_price - (t.get('entry') or t.get('entry_price', 0))) / (t.get('entry') or t.get('entry_price', 0))) * 100, 2
            )

            trades[i]["status"]     = "CLOSED"
            trades[i]["exit_price"] = exit_price
            trades[i]["exit_date"]  = datetime.now().strftime(
                "%Y-%m-%d"
            )
            trades[i]["pnl"]        = pnl
            trades[i]["pnl_pct"]    = pct
            trades[i]["result"]     = ("WIN" if pnl > 0
                                       else "LOSS" if pnl < 0
                                       else "BREAKEVEN")
            trades[i]["close_reason"] = reason

            _save(trades)
            icon = "🟢" if pnl > 0 else "🔴"
            print(f"\n{icon} Trade closed: {symbol}")
            print(f"   Entry  : ₹{t.get('entry') or t.get('entry_price', 0)}  →  "
                  f"Exit : ₹{exit_price}")
            print(f"   P&L    : ₹{pnl:+,.2f}  ({pct:+.2f}%)")
            print(f"   Result : {trades[i]['result']}")
            closed = True
            break

    if not closed:
        print(f"  No open trade found for {symbol}")


def activate_trade(symbol, entry_price, quantity=1):
    """
    Converts a WATCHING trade to OPEN when entry triggers.
    Call this when screener says BUY and you execute.
    """
    trades = _load()
    for i in range(len(trades) - 1, -1, -1):
        t = trades[i]
        if (t["symbol"] == symbol and
                t["status"] == "WATCHING"):
            trades[i]["status"]      = "OPEN"
            trades[i]["entry_price"] = entry_price
            trades[i]["entry"]       = entry_price
            trades[i]["quantity"]    = quantity
            trades[i]["date"]     = datetime.now().strftime(
                "%Y-%m-%d"
            )
            _save(trades)
            print(f"\n✅ Trade ACTIVATED: {symbol} "
                  f"@ ₹{entry_price} x {quantity} shares")
            return

    print(f"  No WATCHING trade found for {symbol}. "
          f"Use log_trade() instead.")


# ════════════════════════════════════════════════════════════
# DISPLAY FUNCTIONS
# ════════════════════════════════════════════════════════════

def show_open_trades():
    """Shows all open and watching positions with live P&L."""
    trades = _load()
    active = [t for t in trades
              if t["status"] in ["OPEN", "WATCHING"]]

    if not active:
        print("\n  No active paper trades.")
        return

    print(f"\n{'═' * 68}")
    print(f"  ACTIVE PAPER TRADES")
    print(f"{'─' * 68}")
    print(f"  {'DATE':<12} {'SYMBOL':<12} {'STATUS':<10} "
          f"{'ENTRY':>8} {'TARGET':>8} {'SL':>8}  LIVE P&L")
    print(f"  {'─' * 64}")

    for t in active:
        # Fetch live price
        try:
            live = round(
                yf.Ticker(t["symbol"] + ".NS")
                  .fast_info["last_price"], 2
            )
            qty    = t.get("quantity", 1)
            unreal = round((live - (t.get('entry') or t.get('entry_price', 0))) * qty, 2)
            pct    = round(
                ((live - (t.get('entry') or t.get('entry_price', 0))) / (t.get('entry') or t.get('entry_price', 0))) * 100, 2
            )
            icon   = "🟢" if unreal >= 0 else "🔴"
            live_str = f"{icon} ₹{unreal:+,.2f} ({pct:+.1f}%)"
        except Exception:
            live_str = "—"

        status_icon = ("👀" if t["status"] == "WATCHING"
                       else "🔄")
        print(f"  {t['date']:<12} {t['symbol']:<12} "
              f"{status_icon} {t['status']:<8} "
              f"₹{(t.get('entry') or t.get('entry_price', 0)):>7}  ₹{t['target']:>7}  "
              f"₹{(t.get('sl') or t.get('stop_loss', 0)):>7}  {live_str}")

        # Show trigger condition for WATCHING trades
        if t["status"] == "WATCHING":
            print(f"  {'':12} 📋 {t['reason'][:55]}...")

    print(f"{'═' * 68}\n")


def show_performance():
    """
    Full performance summary.
    This is what you review weekly to improve your system.
    """
    trades  = _load()
    closed  = [t for t in trades if t["status"] == "CLOSED"]

    if not closed:
        print("\n  No closed trades yet. Keep logging!")
        return

    wins    = [t for t in closed if t["result"] == "WIN"]
    losses  = [t for t in closed if t["result"] == "LOSS"]
    total   = len(closed)
    win_pct = round((len(wins) / total) * 100, 1) if total else 0

    total_pnl  = round(sum(
        t["pnl"] for t in closed if t["pnl"]
    ), 2)
    avg_win    = round(
        sum(t["pnl"] for t in wins) / len(wins), 2
    ) if wins else 0
    avg_loss   = round(
        sum(t["pnl"] for t in losses) / len(losses), 2
    ) if losses else 0
    expectancy = round(
        (win_pct/100 * avg_win) +
        ((1 - win_pct/100) * avg_loss), 2
    )

    print(f"\n{'═' * 52}")
    print(f"  PAPER TRADE PERFORMANCE REPORT")
    print(f"{'─' * 52}")
    print(f"  Total trades     : {total}")
    print(f"  Wins             : {len(wins)}  "
          f"({'🟢' * len(wins)})")
    print(f"  Losses           : {len(losses)}  "
          f"({'🔴' * len(losses)})")
    print(f"  Win rate         : {win_pct}%  "
          f"{'✅' if win_pct >= 50 else '⚠️'}")
    print(f"{'─' * 52}")
    print(f"  Avg win          : ₹{avg_win:+,.2f}")
    print(f"  Avg loss         : ₹{avg_loss:+,.2f}")
    print(f"  Total P&L        : ₹{total_pnl:+,.2f}  "
          f"{'✅' if total_pnl > 0 else '🔴'}")
    print(f"{'─' * 52}")
    print(f"  Expectancy/trade : ₹{expectancy:+,.2f}")

    if expectancy > 0:
        print(f"  ✅ Positive expectancy — system is working")
        print(f"     Keep trading until 20+ trades logged")
        print(f"     before considering real capital")
    else:
        print(f"  ❌ Negative expectancy — review your system")
        print(f"     Do NOT deploy real capital yet")

    print(f"{'─' * 52}")
    print(f"  Phase 5 gate     : need 20+ trades, "
          f"positive expectancy")
    print(f"  Progress         : {total}/20 trades logged")
    gate = (total >= 20 and expectancy > 0 and
            win_pct >= 45)
    print(f"  Gate status      : "
          f"{'✅ PASSED — ready for real capital' if gate else '⏳ Not yet — keep paper trading'}")

    # Trade history
    print(f"\n  {'─' * 48}")
    print(f"  Trade history:")
    print(f"  {'DATE':<12} {'SYMBOL':<12} "
          f"{'RESULT':<10} P&L")
    print(f"  {'─' * 48}")
    for t in closed:
        icon = "🟢" if t["result"] == "WIN" else "🔴"
        print(f"  {t['date']:<12} {t['symbol']:<12} "
              f"{icon} {t['result']:<8} ₹{t['pnl']:+,.2f}")
    print(f"{'═' * 52}\n")


# ════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ════════════════════════════════════════════════════════════

def _load():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return json.load(f)

def _save(trades):
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)

def _next_id():
    trades = _load()
    return len(trades) + 1


# ════════════════════════════════════════════════════════════
# ACTIVE TRADES — update these as signals change
# ════════════════════════════════════════════════════════════

# ── BAJFINANCE — watching for ₹1000 breakout ─────────────
# Already logged. Uncomment only if not yet in JSON file.
# log_trade(
#     symbol   = "BAJFINANCE",
#     action   = "WATCHING",
#     price    = 985.0,
#     target   = 1046.0,
#     sl       = 963.0,
#     quantity = 10,
#     reason   = (
#         "Bull Flag breakout confirmed. H&S head at 1046 ceiling. "
#         "Entry only on close above 1000 with volume > 15M. "
#         "RR tight at 1.0 — small position only."
#     )
# )

# ── RELIANCE — 8/9 score, histogram growing needed ───────
# Uncomment when ready to log:
# log_trade(
#     symbol   = "RELIANCE",
#     action   = "WATCHING",
#     price    = 1435.20,
#     target   = 1506.96,
#     sl       = 1406.50,
#     quantity = 5,
#     reason   = (
#         "8/9 score on master screener. Weekly trend passing "
#         "by 0.52. All conditions met except histogram growing. "
#         "Enter when histogram > 8.49. SL strict at 1406.50."
#     )
# )

# ── To close a trade when target or SL is hit ────────────
# close_trade("RELIANCE",   exit_price=1506.96,
#             reason="Target hit")
# close_trade("BAJFINANCE", exit_price=963.0,
#             reason="SL hit")

# ── To activate a WATCHING trade when entry triggers ─────
# activate_trade("RELIANCE", entry_price=1437.0, quantity=5)

# ════════════════════════════════════════════════════════════
# RUN — shows your current positions every time
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    show_open_trades()
    show_performance()