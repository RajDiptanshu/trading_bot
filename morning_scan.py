"""
MORNING SCAN — runs automatically at 8:55 AM IST
Generates signals and saves report to daily_reports/
Optionally sends Telegram alert if signals found.

Setup: Windows Task Scheduler → runs this script daily
You review the report → paste into Claude.ai for analysis
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf
import ta
import numpy as np
import json
import os
from datetime import datetime, date

# ════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════
from watchlist_reader import load_watchlist
WATCHLIST, SECTORS, _, _, _ = load_watchlist()

REPORT_DIR = "daily_reports"

# ════════════════════════════════════════════════════════════
# INDICATORS
# ════════════════════════════════════════════════════════════

def calc_atr(df, period=14):
    atr = ta.volatility.AverageTrueRange(
        df["High"], df["Low"], df["Close"], window=period
    ).average_true_range()
    return round(atr.iloc[-1], 2)


def fetch_nifty_regime():
    try:
        df = yf.Ticker('^NSEI').history(period='13mo', interval='1d').dropna()
        nifty = df['Close']
        price = nifty.iloc[-1]
        ma50  = nifty.rolling(50).mean().iloc[-1]
        ma200 = nifty.rolling(200).mean().iloc[-1]
        ma200_prev = nifty.rolling(200).mean().iloc[-21]
        f1 = price > ma50
        f2 = price > ma200 and ma200 > ma200_prev
        return {'f1': f1, 'f2': f2, 'nifty_price': round(price, 2)}
    except Exception:
        return {'f1': False, 'f2': False, 'nifty_price': None}


def compute_rs_ranks(watchlist):
    returns = {}
    for sym in watchlist:
        try:
            df = yf.Ticker(sym + '.NS').history(period='13mo', interval='1d').dropna()
            if len(df) < 240:
                continue
            ret = (df['Close'].iloc[-1] - df['Close'].iloc[-252]) / df['Close'].iloc[-252]
            returns[sym] = ret
        except Exception:
            pass
    if not returns:
        return {sym: 50 for sym in watchlist}
    sorted_syms = sorted(returns, key=returns.get)
    n = len(sorted_syms)
    ranks = {}
    for i, sym in enumerate(sorted_syms):
        ranks[sym] = round((i / n) * 100, 1)
    return ranks


def analyse_stock(symbol):
    """Full V8 analysis for one stock."""
    try:
        df = yf.Ticker(symbol + ".NS").history(
            period="13mo", interval="1d"
        ).dropna()
        if len(df) < 240:
            return None

        close = df["Close"]
        price = round(close.iloc[-1], 2)
        vol = df["Volume"]

        # MAs
        ma20 = round(close.rolling(20).mean().iloc[-1], 2)
        ma50 = round(close.rolling(50).mean().iloc[-1], 2)
        ma20_rising = close.rolling(20).mean().iloc[-1] > \
                      close.rolling(20).mean().iloc[-6]

        # RSI
        rsi = round(
            ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1], 1
        )

        # [V10] RSI(2) + MA200 + ADX for the FIXED mean-reversion sleeve.
        # Old rule (RSI14<35 AND price>MA50) fired 0 times in 92,869 stock-days
        # (backtest report 05 section 3) - it was dead code.
        rsi2 = round(ta.momentum.RSIIndicator(close, 2).rsi().iloc[-1], 1)
        ma200 = round(close.rolling(200).mean().iloc[-1], 2) if len(close) >= 200 else None
        try:
            adx14 = round(float(ta.trend.ADXIndicator(
                df["High"], df["Low"], close, 14).adx().iloc[-1]), 1)
        except Exception:
            adx14 = None

        # MACD
        macd_obj = ta.trend.MACD(close, 12, 26, 9)
        macd_val = macd_obj.macd().iloc[-1]
        macd_sig = macd_obj.macd_signal().iloc[-1]
        macd_bull = bool(macd_val > macd_sig)
        hist = float(macd_obj.macd_diff().iloc[-1])

        # ATR
        atr = float(calc_atr(df))
        atr_pct = round(atr / price * 100, 2)

        # Bollinger Bands
        bb = ta.volatility.BollingerBands(close, 20, 2)
        lower_bb = round(float(bb.bollinger_lband().iloc[-1]), 2)
        upper_bb = round(float(bb.bollinger_hband().iloc[-1]), 2)

        # Faster vectorized OBV
        direction_series = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv_series = (vol * direction_series).cumsum()
        obv_now = obv_series.iloc[-1]
        obv_10d = obv_series.iloc[-11]
        obv_rising = bool(obv_now > obv_10d)

        # Volume ratio
        vol_ratio = round(
            vol.iloc[-1] / vol.rolling(20).mean().iloc[-1], 2
        )

        # %RANGE (stock personality)
        pct_range = round(
            ((df["High"] - df["Low"]) / close).rolling(20).mean().iloc[-1], 4
        )

        ma50_rising = close.rolling(50).mean().iloc[-1] > close.rolling(50).mean().iloc[-21]

        a1 = 2 if (price > ma50 and ma20 > ma50) else 0
        a2 = 1 if (ma20_rising and ma50_rising) else 0
        trend_score = a1 + a2

        high_52w = df['High'].rolling(252).max().iloc[-1]
        low_52w  = df['Low'].rolling(252).min().iloc[-1]

        b1 = 0
        b2 = 1 if high_52w > 0 and (high_52w - price) / high_52w <= 0.25 else 0
        b3 = 1 if low_52w > 0 and price >= low_52w * 1.30 else 0
        rs_score = b1 + b2 + b3

        c1 = 1 if 40 <= rsi <= 65 else 0
        oscillator_score = c1

        vol_ratio = round(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1], 2)
        d1 = 1 if vol_ratio >= 1.0 else 0
        d2 = 1 if obv_rising else 0

        recent_high = df['High'].rolling(20).max().iloc[-6:-1].max()
        breakout_candles = []
        for k in range(-5, 0):
            if df['High'].iloc[k] >= recent_high:
                breakout_candles.append(k)
        if breakout_candles:
            best_k = breakout_candles[-1]
            d3 = 1 if vol.iloc[best_k] >= vol.rolling(20).mean().iloc[best_k] * 1.5 else 0
        else:
            d3 = 0
        volume_score = d1 + d2 + d3

        pct_range = round(((df['High'] - df['Low']) / close).rolling(20).mean().iloc[-1], 4)
        e1 = 1 if 0.015 <= pct_range <= 0.055 else 0
        volatility_score = e1

        f1 = 0
        f2 = 0
        regime_score = f1 + f2

        score = trend_score + rs_score + oscillator_score + volume_score + volatility_score + regime_score

        # [V10] fixed MR rule: RSI(2)<10 (classic short-term MR, Kestner ch8)
        # + long-term uptrend (>MA200) + ADX<32 (MR regime, Kestner ch6).
        # SHADOW-ONLY: logged for forward validation, never an order.
        mr_signal = (rsi2 < 10 and ma200 is not None and price > ma200
                     and (adx14 is None or adx14 < 32))

        conditions_met = []
        strategy  = 'WAIT'
        direction = '—'

        short_c1 = price < ma20 and price < ma50
        short_c2 = not ma20_rising
        short_c3 = 40 <= rsi <= 65
        short_c4 = not macd_bull
        short_score_raw = sum([short_c1, short_c2, short_c3, short_c4])

        if mr_signal:
            # [V10] shadow-only: visible in the report, but no direction/order
            # until the new rule is forward-validated (report 05 section 3).
            strategy  = 'MEAN_REVERSION (SHADOW)'
            direction = '—'
            conditions_met = [f'RSI2 {rsi2}', f'>MA200 {ma200}',
                              f'ADX {adx14 if adx14 is not None else "n/a"}']
        if score >= 9:
            strategy  = 'MOMENTUM'
            direction = 'LONG'
            if a1: conditions_met.append('Price>MA20>MA50')
            if a2: conditions_met.append('Both MAs rising')
            if b2: conditions_met.append('Near 52W high')
            if b3: conditions_met.append('30%+ above 52W low')
            if c1: conditions_met.append(f'RSI {rsi}')
            if d1: conditions_met.append(f'Vol {vol_ratio}x')
            if d2: conditions_met.append('OBV rising')
            if d3: conditions_met.append('Breakout vol')
            if e1: conditions_met.append(f'%Range {pct_range}')
        elif short_score_raw >= 3:
            strategy  = 'SHORT'
            direction = 'SHORT'
            score = short_score_raw + 2
            if short_c1: conditions_met.append('Price<MA20+MA50')
            if short_c2: conditions_met.append('MA20 falling')
            if short_c3: conditions_met.append(f'RSI {rsi}')
            if short_c4: conditions_met.append('MACD bearish')

        # ATR-based levels
        atr_sl = round(price - 2 * atr, 2) if direction == "LONG" \
            else round(price + 2 * atr, 2) if direction == "SHORT" \
            else None
        atr_target = round(price + 3 * atr, 2) if direction == "LONG" \
            else round(price - 3 * atr, 2) if direction == "SHORT" \
            else None

        return {
            'symbol':              symbol,
            'price':               price,
            'sector':              SECTORS.get(symbol, 'Other'),
            'ma20':                ma20,
            'ma50':                ma50,
            'rsi':                 rsi,
            'macd_bull':           macd_bull,
            'hist':                round(hist, 2),
            'atr':                 atr,
            'atr_pct':             atr_pct,
            'lower_bb':            lower_bb,
            'upper_bb':            upper_bb,
            'obv_rising':          obv_rising,
            'vol_ratio':           vol_ratio,
            'pct_range':           pct_range,
            'high_52w':            round(high_52w, 2),
            'low_52w':             round(low_52w, 2),
            'pct_from_52w_high':   round((high_52w - price) / high_52w * 100, 1) if high_52w > 0 else None,
            'rs_rank':             50,
            'trend_score':         trend_score,
            'rs_score':            rs_score,
            'oscillator_score':    oscillator_score,
            'volume_score':        volume_score,
            'volatility_score':    volatility_score,
            'regime_score':        regime_score,
            'score':               score,
            'strategy':            strategy,
            'direction':           direction,
            'conditions':          conditions_met,
            'atr_sl':              atr_sl,
            'atr_target':          atr_target,
            'mr_signal':           mr_signal,
            'rsi2':                rsi2,
            'ma200':               ma200,
            'adx':                 adx14,
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def run_morning_scan():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M %p")

    print(f"\n{'═'*66}")
    print(f"  📊 MORNING SCAN — {date_str} {time_str}")
    print(f"  V9 rules: 14-pt weighted | RS rank | Regime gate")
    print(f"{'═'*66}")

    print('  Computing RS ranks across watchlist...')
    rs_ranks = compute_rs_ranks(WATCHLIST)

    print('  Fetching Nifty regime...')
    nifty_regime = fetch_nifty_regime()
    regime_pts = (1 if nifty_regime['f1'] else 0) + (1 if nifty_regime['f2'] else 0)
    regime_icon = '🟢' if regime_pts == 2 else '🟡' if regime_pts == 1 else '🔴'
    print(f'  {regime_icon} Nifty {nifty_regime["nifty_price"]} | Regime score: {regime_pts}/2')

    print('  Fetching India VIX...')
    try:
        india_vix = round(yf.Ticker('^INDIAVIX').history(period='1d')['Close'].iloc[-1], 2)
        print(f'  India VIX: {india_vix}')
    except Exception:
        india_vix = None
        print('  ⚠️  Could not fetch India VIX')

    if regime_pts == 0:
        effective_min_score = 11
        print(f'  ⚠️  Regime 0/2 — minimum score raised to {effective_min_score}/14')
    elif regime_pts == 1:
        effective_min_score = 10
        print(f'  ⚠️  Regime 1/2 — minimum score raised to {effective_min_score}/14')
    else:
        effective_min_score = 9

    results = []
    for sym in WATCHLIST:
        print(f'  Scanning {sym}...', end='\r')
        r = analyse_stock(sym)
        if not r or 'error' in r:
            if r:
                results.append(r)
            continue

        rank = rs_ranks.get(sym, 50)
        r['rs_rank'] = rank
        b1 = 2 if rank >= 75 else 1 if rank >= 50 else 0
        r['rs_score'] = r['rs_score'] + b1

        # 3. Final Score & Verdict (Regime-aware)
        r['regime_score'] = regime_pts
        r['score'] = (r['trend_score'] + r['rs_score'] +
                      r['oscillator_score'] + r['volume_score'] +
                      r['volatility_score'] + r['regime_score'])

        # [V10] F1 HARD VETO (CLAUDE.md section 4 spec; backtest report 05:
        # PF 1.16 -> 1.26, expectancy Rs214 -> Rs314, DD 15.2% -> 12.9%).
        # No new LONGs of any kind while Nifty < its 50dma. Raising the
        # threshold alone (old behaviour) still let regime-0 longs through.
        if not nifty_regime['f1'] and r['direction'] == 'LONG':
            r['verdict'] = 'F1 VETO (Nifty<50dma) - no new longs'
            r['strategy'] = 'WAIT'
            r['direction'] = '—'
            r['atr_sl'] = None
            r['atr_target'] = None
        elif r['score'] >= 11:
            r['verdict'] = '✅ STRONG BUY' if r['direction'] == 'LONG' else '✅ STRONG SHORT'
        elif r['score'] >= effective_min_score:
            r['verdict'] = f'🟡 CONSIDER ({r["strategy"]})'
        else:
            r['verdict'] = '⏳ WAIT'
            r['strategy'] = 'WAIT'
            r['direction'] = '—'
            r['atr_sl'] = None
            r['atr_target'] = None

        results.append(r)

    # Sort by score descending
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["score"], reverse=True)

    # ── Print report ──────────────────────────────────────
    print(f"\n{'═'*66}")
    print(f"  SIGNALS — {date_str}")
    print(f"{'═'*66}")

    buys = [r for r in valid if 'error' not in r and (r['score'] >= effective_min_score and r['direction'] == 'LONG')]
    waits = [r for r in valid if r["score"] < 9]

    if buys:
        print(f"\n  🚨 ACTIVE SIGNALS ({len(buys)}):")
        print(f"  {'Symbol':<12} {'Price':>8} {'Score':>6} "
              f"{'Strategy':<14} {'SL':>8} {'Target':>8} "
              f"{'Verdict'}")
        print(f"  {'─'*76}")
        for r in buys:
            sl_str  = f"₹{r['atr_sl']:>7}"  if r['atr_sl']  is not None else '    N/A'
            tgt_str = f"₹{r['atr_target']:>7}" if r['atr_target'] is not None else '    N/A'
            print(f"  {r['symbol']:<12} ₹{r['price']:>7} "
                  f"{r['score']:>4}/14 {r['strategy']:<14} "
                  f"{sl_str} {tgt_str}  "
                  f"{r['verdict']}")
            print(f"  {'':12} Conditions: "
                  f"{', '.join(r['conditions'])}")
    else:
        print(f"\n  No active signals today. System says WAIT.")
        print(f"  Patience is a trading strategy.")

    # Summary table
    print(f"\n{'─'*66}")
    print(f"  FULL WATCHLIST")
    print(f"{'─'*66}")
    print(f"  {'Symbol':<12} {'Price':>8} {'RSI':>6} {'MACD':>6} "
          f"{'ATR%':>6} {'Score':>6} {'Strategy'}")
    print(f"  {'─'*62}")
    for r in valid:
        if "error" in r:
            continue
        macd_icon = "🟢" if r["macd_bull"] else "🔴"
        print(f"  {r['symbol']:<12} ₹{r['price']:>7} "
              f"{r['rsi']:>5.1f} {macd_icon}    "
              f"{r['atr_pct']:>5.1f}% {r['score']:>4}/14 "
              f"{r['strategy']}")

    # Save report to file
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"scan_{date_str}.json")
    with open(report_file, "w") as f:
        json.dump({
        "date": date_str,
        "time": time_str,
        "india_vix": india_vix,
        "signals": [r for r in valid if r["score"] >= effective_min_score],
        "all_stocks": valid,
    }, f, indent=2, default=str)
    print(f"\n  Report saved: {report_file}")

    # Save text report for pasting into Claude
    text_file = os.path.join(REPORT_DIR, f"scan_{date_str}.txt")
    with open(text_file, "w", encoding="utf-8") as f:
        f.write(f"MORNING SCAN — {date_str} {time_str}\n")
        f.write("="*66 + "\n\n")
        if buys:
            f.write(f"ACTIVE SIGNALS ({len(buys)}):\n")
            for r in buys:
                f.write(f"  {r['symbol']} ₹{r['price']} "
                        f"Score:{r['score']}/14 {r['strategy']} "
                        f"{r['direction']} "
                        f"SL:₹{r['atr_sl']} "
                        f"Target:₹{r['atr_target']}\n")
                f.write(f"    Conditions: "
                        f"{', '.join(r['conditions'])}\n")
        else:
            f.write("No signals. WAIT.\n")
        f.write(f"\nFULL SCAN:\n")
        for r in valid:
            if "error" not in r:
                f.write(f"  {r['symbol']:<12} ₹{r['price']:>7} "
                        f"RSI:{r['rsi']} Score:{r['score']}/14 "
                        f"{r['strategy']}\n")
    print(f"  Text report: {text_file}")
    print(f"\n  ► Copy {text_file} contents and paste")
    print(f"    into Claude.ai for analysis and trade decision")
    print(f"{'═'*66}\n")

    return valid


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("morning_scan", "START")
    try:
        run_morning_scan()
        record_heartbeat("morning_scan", "FINISH")
    except Exception:
        record_heartbeat("morning_scan", "ERROR")
        raise