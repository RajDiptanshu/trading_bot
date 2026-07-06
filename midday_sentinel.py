"""
MIDDAY SENTINEL — Pure Python, Zero AI
========================================
Runs at 11:00 AM daily via Task Scheduler.
Completes in under 90 seconds.

PURPOSE:
  The morning crew enters trades at 9:10 AM based on 9:00 AM data.
  This sentinel checks open positions at 11 AM for breaking news
  that emerged AFTER the trade was placed.

  Examples of what this catches:
    - RBI surprise rate decision at 10 AM
    - SEBI order on an open position at 9:30 AM
    - Promoter pledge news at 10:30 AM
    - Earnings miss released post-open
    - Sudden India VIX spike > 20

  If dangerous news is detected:
    → Writes WARNING flag to paper_trades.json (sentinel_flag field)
    → Position monitor at 3:30 PM reads the flag and closes early
    → Decision log updated with reason

NO LLM. NO API CALLS. NO TOKENS.
  Uses yfinance .news (Yahoo Finance aggregator)
  + DuckDuckGo for confirmation
  Pure keyword matching against kill words.
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import yfinance as yf
import os
import pyotp
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from SmartApi import SmartConnect
from watchlist_reader import load_watchlist

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# SET TO False ONLY WHEN DEPLOYING REAL CAPITAL
PAPER_TRADING_MODE = True

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).parent
TRADES_FILE = BASE_DIR / "paper_trades.json"
MEMORY_FILE = BASE_DIR / "strategy_memory.json"
LOG_FILE    = BASE_DIR / "decision_log.json"
FLAGS_FILE  = BASE_DIR / "sentinel_flags.json"

# Hard-coded kill words — position is flagged immediately
KILL_WORDS = [
    "fraud", "scam", "sebi ban", "sebi order", "sebi notice",
    "ed raid", "cbi raid", "income tax raid",
    "promoter pledge", "pledge shares", "pledge invoked",
    "accounting irregularit", "audit qualif",
    "bankruptcy", "insolvency", "default", "debt restructur",
    "delisting", "suspend trading",
    "profit warning", "massive loss", "quarterly loss",
    "ceo resign", "md resign", "chairman resign",
    "fir filed", "arrest", "money laundering",
    "downgrade", "credit rating cut",
    "earnings miss", "below estimate", "below expectations",
    "weak guidance", "guidance cut", "guidance withdrawn",
    "fpi selling", "margin call", "credit watch",
]

# Caution words — flag for review but don't force close
CAUTION_WORDS = [
    "investigation", "probe", "notice", "penalty",
    "block deal", "bulk deal", "fii selling",
    "margin pressure", "revenue decline",
    "stake sale", "promoter selling",
]

# Market-wide risk triggers
MARKET_KILL_WORDS = [
    "circuit breaker", "market halt", "trading suspend",
    "rbi rate hike surprise", "budget shock",
    "geopolitical crisis", "war", "nuclear",
]

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
    if not MEMORY_FILE.exists():
        return {}
    with open(MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_flags(flags: dict):
    with open(FLAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)

def append_sentinel_log(flags: dict, summary: dict):
    log_data = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            log_data = []

    log_data.append({
        "date":    date.today().isoformat(),
        "time":    datetime.now().strftime("%H:%M"),
        "type":    "MIDDAY_SENTINEL",
        "summary": summary,
        "flags":   flags,
    })

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)

# ══════════════════════════════════════════════════════════════
# YFINANCE NEWS FETCH
# ══════════════════════════════════════════════════════════════

def fetch_yfinance_news(symbol: str, hours_back: int = 6) -> list:
    """
    Fetches recent news from Yahoo Finance via yfinance.
    Returns list of recent headlines as strings.
    Free. Structured. Timestamped.
    """
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - (hours_back * 3600)
        ticker = yf.Ticker(symbol + ".NS")
        news_items = ticker.news or []

        recent_headlines = []
        for item in news_items:
            pub_time = item.get("providerPublishTime", 0)
            if pub_time >= cutoff:
                title = item.get("title", "")
                if title:
                    recent_headlines.append(title.lower())

        return recent_headlines

    except Exception:
        return []

# ══════════════════════════════════════════════════════════════
# DUCKDUCKGO CONFIRMATION
# ══════════════════════════════════════════════════════════════

def ddg_confirm(symbol: str) -> list:
    """
    DuckDuckGo search for additional confirmation.
    Only called when yfinance already found a potential issue.
    Returns list of headlines.
    """
    if not DDGS_AVAILABLE:
        return []

    try:
        ddgs = DDGS()
        results = list(ddgs.text(
            f"{symbol} NSE news last 6 hours",
            max_results=3
        ))
        return [r["title"].lower() for r in results]
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════
# INDIA VIX CHECK
# ══════════════════════════════════════════════════════════════
def check_india_vix(thresholds: dict) -> tuple:
    """
    Fetches India VIX from yfinance.
    Returns (vix_value: float, is_dangerous: bool)
    """
    danger_threshold = thresholds.get('high_vol_floor', 18.0)
    try:
        vix = yf.Ticker("^INDIAVIX")
        hist = vix.history(period="1d", interval="5m")
        if not hist.empty:
            current_vix = round(float(hist["Close"].iloc[-1]), 2)
            is_dangerous = current_vix >= danger_threshold
            return current_vix, is_dangerous
    except Exception:
        pass

    # Fallback: DuckDuckGo search
    if DDGS_AVAILABLE:
        try:
            ddgs = DDGS()
            results = list(ddgs.text("India VIX today current level", max_results=1))
            # Even if we can't parse the number easily, returning False is safer
            return None, False 
        except Exception:
            pass

    return None, False # Unknown VIX = Clear


def emergency_exit(symbol: str, quantity: int, angel_tokens: dict) -> bool:
    if PAPER_TRADING_MODE:
        print(f'  [PAPER MODE] Emergency exit skipped for {symbol} — PAPER_TRADING_MODE is True')
        return False
    try:
        obj = SmartConnect(api_key=os.getenv('ANGEL_API_KEY'))
        totp = pyotp.TOTP(os.getenv('ANGEL_TOTP_SECRET')).now()
        data = obj.generateSession(os.getenv('ANGEL_CLIENT_ID'), os.getenv('ANGEL_PASSWORD'), totp)
        if not data.get('status'):
            print(f'  Emergency exit FAILED — Angel One login failed: {data.get("message")}')
            return False
        token = angel_tokens.get(symbol)
        if not token:
            print(f'  Emergency exit FAILED — no Angel token for {symbol}')
            return False
        resp = obj.placeOrder({
            'variety': 'NORMAL',
            'tradingsymbol': f'{symbol}-EQ',
            'symboltoken': token,
            'transactiontype': 'SELL',
            'exchange': 'NSE',
            'ordertype': 'MARKET',
            'producttype': 'DELIVERY',
            'duration': 'DAY',
            'quantity': str(quantity),
        })
        print(f'  Emergency exit PLACED for {symbol}: {resp}')
        return True
    except Exception as e:
        print(f'  Emergency exit ERROR for {symbol}: {e}')
        return False

def scan_headlines(headlines: list, symbol: str) -> dict:
    """Scans headline list against kill/caution word lists."""
    matched_kill    = [kw for kw in KILL_WORDS    if any(kw in h for h in headlines)]
    matched_caution = [kw for kw in CAUTION_WORDS if any(kw in h for h in headlines)]
    return {
        "kill_triggered":    bool(matched_kill),
        "caution_triggered": bool(matched_caution),
        "matched_kill":      matched_kill,
        "matched_caution":   matched_caution,
        "headline_sample":   headlines[0] if headlines else "",
    }


def check_market_wide_news() -> dict:
    """Checks DuckDuckGo for market-wide crisis keywords."""
    if not DDGS_AVAILABLE:
        return {"triggered": False, "reason": ""}
    try:
        results = list(DDGS().text("Nifty market halt circuit breaker India today", max_results=3))
        headlines = [r["title"].lower() for r in results]
        matched = [kw for kw in MARKET_KILL_WORDS if any(kw in h for h in headlines)]
        return {"triggered": bool(matched), "reason": matched[0] if matched else ""}
    except Exception:
        return {"triggered": False, "reason": ""}


def check_nifty_regime_intraday() -> dict:
    """Checks if Nifty is trading below its 50-day MA."""
    try:
        import yfinance as yf
        df = yf.Ticker("^NSEI").history(period="60d", interval="1d").dropna()
        price = round(float(df["Close"].iloc[-1]), 2)
        ma50  = round(float(df["Close"].rolling(50).mean().iloc[-1]), 2)
        breach = price < ma50
        return {
            "breach":      breach,
            "nifty_price": price,
            "ma50":        ma50,
            "reason":      f"Nifty {price} below MA50 {ma50}" if breach else "",
        }
    except Exception:
        return {"breach": False, "nifty_price": None, "ma50": None, "reason": ""}


def main():
    start_time = datetime.now()
    today = date.today().isoformat()

    print(f"\n{'═'*60}")
    print(f"  🛡️  MIDDAY SENTINEL — {today} 11:00 AM")
    print(f"  Pure Python. Zero AI. Keyword matching.")
    print(f"{'═'*60}\n")

    # Load open positions
    all_trades   = load_trades()
    open_trades  = [t for t in all_trades if t.get("status") == "OPEN"]
    memory       = load_memory()
    
    # Load watchlist for tokens
    _, _, angel_tokens, _, _ = load_watchlist()

    vix_thresholds        = memory.get('instrument_selection', {}).get('india_vix_thresholds', {})
    VIX_CAUTION_THRESHOLD = vix_thresholds.get('high_vol_floor', 18.0)
    VIX_DANGER_THRESHOLD  = vix_thresholds.get('extreme_vol_floor', 22.0)
    VIX_EXTREME_THRESHOLD = memory.get('news_filters', {}).get('india_vix_spike_threshold', 24.0)

    is_paper = not memory.get("meta", {}).get("deployment_ready", False)

    # Merge kill words from strategy_memory if available
    extra_kill = memory.get("news_filters", {}).get("always_skip_keywords", [])
    for kw in extra_kill:
        if kw.lower() not in KILL_WORDS:
            KILL_WORDS.append(kw.lower())

    if not open_trades:
        print("  ℹ️  No open positions. Nothing to monitor.")
        append_sentinel_log({}, {"open_positions": 0, "flags_raised": 0})
        return

    print(f"  Monitoring {len(open_trades)} open position(s)...\n")

    # ── Check India VIX ─────────────────────────────────────
    print("  Checking India VIX...")
    vix_value, vix_dangerous = check_india_vix(vix_thresholds)
    if vix_value:
        vix_icon = "🔴" if vix_dangerous else "🟢"
        print(f"  {vix_icon} India VIX: {vix_value}"
              f"  {'⚠️  DANGER THRESHOLD EXCEEDED' if vix_dangerous else 'Normal'}")
    else:
        print("  ⚠️  VIX fetch failed — continuing without VIX data")
        vix_dangerous = False

    # ── Check market-wide events ─────────────────────────────
    print("\n  Checking market-wide news...")
    market_check = check_market_wide_news()
    if market_check["triggered"]:
        print(f"  🔴 MARKET EVENT: {market_check['reason']}")
    else:
        print("  ✅ No market-wide events detected")

    print('\n  Checking Nifty intraday regime...')
    regime_check = check_nifty_regime_intraday()
    if regime_check['breach']:
        print(f'  🔴 REGIME BREACH: {regime_check["reason"]}')
    else:
        if regime_check['nifty_price']:
            print(f'  ✅ Nifty {regime_check["nifty_price"]} above MA50 {regime_check["ma50"]} — regime intact')

    # ── Per-position news check ──────────────────────────────
    print(f"\n  {'Symbol':<12} {'yFin News':>10} {'Status':<30} Matched")
    print(f"  {'─'*70}")

    flags = {}
    flagged_count = 0

    for trade in open_trades:
        sym = trade["symbol"]

        # Fetch yfinance news (last 6 hours)
        headlines = fetch_yfinance_news(sym, hours_back=6)

        # If market-wide event, treat all positions as cautioned
        if market_check["triggered"]:
            headlines.append(market_check["reason"].lower())

        # If VIX dangerous, add flag
        if vix_dangerous and vix_value:
            headlines.append(f"india vix spike {vix_value} danger threshold exceeded")

        if regime_check['breach'] and trade.get('direction') == 'LONG':
            headlines.append(regime_check['reason'].lower())

        scan = scan_headlines(headlines, sym)

        # Get DuckDuckGo confirmation only if yfinance found something
        if scan["kill_triggered"] and DDGS_AVAILABLE:
            ddg_headlines = ddg_confirm(sym)
            ddg_scan = scan_headlines(ddg_headlines, sym)
            # Require at least one source to confirm kill
            confirmed = scan["kill_triggered"] and (
                ddg_scan["kill_triggered"] or
                ddg_scan["caution_triggered"]
            )
        else:
            confirmed = scan["kill_triggered"]

        # Determine flag level
        if confirmed:
            flag_level = "KILL"
            icon = "🔴"
            flagged_count += 1
        elif scan["caution_triggered"] or market_check["triggered"]:
            flag_level = "CAUTION"
            icon = "🟡"
            flagged_count += 1
        else:
            flag_level = "CLEAR"
            icon = "🟢"

        news_count = len(headlines)
        status_txt = (
            f"KILL — {scan['matched_kill'][:2]}" if flag_level == "KILL"
            else f"CAUTION — {scan['matched_caution'][:2]}" if flag_level == "CAUTION"
            else "Clear"
        )

        print(f"  {icon} {sym:<12} {news_count:>3} items   {status_txt}")
        if scan["headline_sample"]:
            print(f"     └─ \"{scan['headline_sample'][:80]}\"")

        # Record flag
        if flag_level != "CLEAR":
            flags[sym] = {
                "flag_level":      flag_level,
                "flagged_at":      datetime.now().strftime("%H:%M"),
                "kill_keywords":   scan["matched_kill"],
                "caution_keywords": scan["matched_caution"],
                "headline_sample": scan["headline_sample"],
                "vix_dangerous":   vix_dangerous,
                "vix_value":       vix_value,
                "action": (
                    "IMMEDIATE_EXIT"
                    if flag_level == "KILL"
                    else "MONITOR_CLOSELY"
                ),
            }
            
            # EMERGENCY EXIT IF KILL
            if flag_level == "KILL":
                trade_qty = trade.get('quantity', 1)
                exited = emergency_exit(sym, trade_qty, angel_tokens)
                if exited:
                    trade['status'] = 'CLOSED'
                    trade['exit_date'] = datetime.now().strftime('%Y-%m-%d')
                    trade['exit_reason'] = f'SENTINEL_EMERGENCY_EXIT: {scan["matched_kill"]}'
                    flags[sym]['action'] = 'EMERGENCY_EXITED'
                else:
                    flags[sym]['action'] = 'CLOSE_AT_3:30PM'

    # ── Update paper_trades.json with sentinel flags ─────────
    if flags:
        print(f"\n  {'─'*60}")
        print(f"  ⚠️  {flagged_count} position(s) flagged")

        for trade in all_trades:
            sym = trade["symbol"]
            if sym in flags and trade.get("status") == "OPEN":
                # This part is now redundant for KILL flags as they are CLOSED above, 
                # but good to keep for CAUTION flags.
                trade["sentinel_flag"]   = flags[sym]["flag_level"]
                trade["sentinel_reason"] = (
                    f"Kill words: {flags[sym]['kill_keywords']}"
                    if flags[sym]["kill_keywords"]
                    else f"Caution: {flags[sym]['caution_keywords']}"
                )
                trade["sentinel_time"]   = flags[sym]["flagged_at"]

                action = flags[sym]["action"]
                print(f"  {sym}: {flags[sym]['flag_level']} → {action}")

        save_trades(all_trades)
        save_flags(flags)
        print(f"\n  Emergency exits processed. Remaining CAUTION flags will be reviewed by position_monitor.py.")

    else:
        print(f"\n  ✅ All {len(open_trades)} position(s) clear. No flags raised.")

    # ── Log results ──────────────────────────────────────────
    summary = {
        "open_positions":  len(open_trades),
        "flags_raised":    flagged_count,
        "kill_flags":      sum(1 for f in flags.values()
                               if f["flag_level"] == "KILL"),
        "caution_flags":   sum(1 for f in flags.values()
                               if f["flag_level"] == "CAUTION"),
        "india_vix":       vix_value,
        "vix_dangerous":   vix_dangerous,
        "market_event":    market_check.get("reason", "none"),
    }
    append_sentinel_log(flags, summary)

    # ── Summary ──────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'═'*60}")
    print(f"  SENTINEL COMPLETE — {elapsed}s | Zero AI used")
    print(f"  Flags file: sentinel_flags.json")
    print(f"  Next check: position_monitor.py at 3:30 PM")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("midday_sentinel", "START")
    try:
        main()
        record_heartbeat("midday_sentinel", "FINISH")
    except Exception:
        record_heartbeat("midday_sentinel", "ERROR")
        raise