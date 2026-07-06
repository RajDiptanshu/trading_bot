import yfinance as yf

# ════════════════════════════════════════════════════════════
# BLOCK 1 — Live price fetcher
# ════════════════════════════════════════════════════════════
def get_live_price(symbol):
    """Fetches current market price from Yahoo Finance."""
    ticker = yf.Ticker(symbol + ".NS")
    return round(ticker.fast_info["last_price"], 2)

# ════════════════════════════════════════════════════════════
# BLOCK 2 — Risk-reward calculator
# ════════════════════════════════════════════════════════════
def calculate_rr(entry, target, stop_loss):
    """Returns RR ratio. Minimum acceptable = 2.0"""
    reward = target - entry
    risk   = entry - stop_loss
    if risk <= 0:
        return 0
    return round(reward / risk, 2)

# ════════════════════════════════════════════════════════════
# BLOCK 3 — Trend filter using moving averages
# ════════════════════════════════════════════════════════════
def get_trend_signal(symbol):
    """
    Classifies trend using 20-day and 50-day moving averages.
    UPTREND   = price above both MAs + MA20 rising
    DOWNTREND = price below both MAs
    MIXED     = conflicting signals — avoid
    """
    df    = yf.Ticker(symbol + ".NS").history(period="3mo", interval="1d")
    close = df["Close"]
    ma20  = close.rolling(window=20).mean().iloc[-1]
    ma50  = close.rolling(window=50).mean().iloc[-1]
    price = close.iloc[-1]

    above_ma20  = price > ma20
    above_ma50  = price > ma50
    ma20_rising = close.rolling(20).mean().iloc[-1] > close.rolling(20).mean().iloc[-5]

    if above_ma20 and above_ma50 and ma20_rising:
        trend = "🟢 UPTREND"
    elif not above_ma20 and not above_ma50:
        trend = "🔴 DOWNTREND"
    else:
        trend = "🟡 MIXED"

    return {
        "symbol": symbol,
        "price":  round(price, 2),
        "ma20":   round(ma20, 2),
        "ma50":   round(ma50, 2),
        "trend":  trend
    }

# ════════════════════════════════════════════════════════════
# WATCHLIST — Edit this list to track your own stocks
# ════════════════════════════════════════════════════════════
watchlist = [
    {"symbol": "HDFCBANK",   "target_pct": 0.05, "sl_pct": 0.02},
    {"symbol": "RELIANCE",   "target_pct": 0.06, "sl_pct": 0.025},
    {"symbol": "INFY",       "target_pct": 0.05, "sl_pct": 0.02},
    {"symbol": "TCS",        "target_pct": 0.05, "sl_pct": 0.02},
    {"symbol": "BAJFINANCE", "target_pct": 0.06, "sl_pct": 0.025},
]

symbols = [s["symbol"] for s in watchlist]

# ════════════════════════════════════════════════════════════
# OUTPUT 1 — Risk-Reward Table
# ════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
print("  WATCHLIST SCREENER — Risk / Reward Analysis")
print("=" * 62)
print(f"{'SYMBOL':<12} {'PRICE':>8} {'TARGET':>8} {'SL':>8} {'RR':>5}  SIGNAL")
print("-" * 62)

for stock in watchlist:
    try:
        entry  = get_live_price(stock["symbol"])
        target = round(entry * (1 + stock["target_pct"]), 2)
        sl     = round(entry * (1 - stock["sl_pct"]), 2)
        rr     = calculate_rr(entry, target, sl)
        signal = "✅ GOOD" if rr >= 2 else "❌ SKIP"
        print(f"{stock['symbol']:<12} {entry:>8} {target:>8} "
              f"{sl:>8} {rr:>5}  {signal}")
    except Exception as e:
        print(f"{stock['symbol']:<12} ERROR: {e}")

print("-" * 62)
print("  Rule: Only consider trades with RR >= 2.0")

# ════════════════════════════════════════════════════════════
# OUTPUT 2 — Trend Filter Table
# ════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
print("  TREND FILTER — Moving Average Analysis")
print("=" * 62)
print(f"{'SYMBOL':<12} {'PRICE':>8} {'MA20':>8} {'MA50':>8}  TREND")
print("-" * 62)

for symbol in symbols:
    try:
        s = get_trend_signal(symbol)
        print(f"{s['symbol']:<12} {s['price']:>8} "
              f"{s['ma20']:>8} {s['ma50']:>8}  {s['trend']}")
    except Exception as e:
        print(f"{symbol:<12} ERROR: {e}")

print("-" * 62)
print("  Rule: Only trade 🟢 UPTREND stocks. Skip 🔴 and 🟡")

# ════════════════════════════════════════════════════════════
# OUTPUT 3 — Combined final verdict
# ════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
print("  FINAL VERDICT — Stocks worth analysing today")
print("=" * 62)

for stock in watchlist:
    try:
        entry  = get_live_price(stock["symbol"])
        target = round(entry * (1 + stock["target_pct"]), 2)
        sl     = round(entry * (1 - stock["sl_pct"]), 2)
        rr     = calculate_rr(entry, target, sl)
        t      = get_trend_signal(stock["symbol"])

        good_rr    = rr >= 2
        good_trend = "UPTREND" in t["trend"]

        if good_rr and good_trend:
            verdict = "🚀 ANALYSE FURTHER"
        elif good_trend and not good_rr:
            verdict = "⏳ WAIT — trend good, RR weak"
        elif good_rr and not good_trend:
            verdict = "⚠️  SKIP — RR good, trend bad"
        else:
            verdict = "❌ SKIP"

        print(f"{stock['symbol']:<12}  {verdict}")
    except Exception as e:
        print(f"{stock['symbol']:<12} ERROR: {e}")

print("=" * 62)
print("  This scan runs every morning before 9:15 AM\n")