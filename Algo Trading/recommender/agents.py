"""
Agents for the recommendation engine — V10.
  Agent 1  TechnicalAgent   — pure Python: V9 14-pt score + Kestner confirmations,
                              ADX strategy routing, fixed MR sleeve, hybrid exits,
                              Kelly-aware sizing, optional ML probability (ML4T).
  Agent 2  FundamentalAgent — news (yfinance + DuckDuckGo), LM-style sentiment score,
                              kill-words, wider earnings window, quality score.
  Agent 3  Strategist       — ONE Claude call, strict JSON, EV-first playbook
                              (Sinclair), Cohen vol-matrix for options, sanitizer.
All math is Python. Claude never computes numbers.

V10 changes are tagged [V10] with the source of evidence:
  (BT)  = own backtest report 05 (2026-06-10)
  (K)   = Kestner, Quantitative Trading Strategies (Book3)
  (S)   = Sinclair, Option Trading (Book2)
  (C)   = Cohen, The Bible of Options Strategies (book1)
  (ML4T)= Jansen, machine-learning-for-trading repo
"""
from __future__ import annotations
import os, json, time, math
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import live_quotes as lq          # Angel One historical candles (yfinance fallback inside)

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))   # C:\trading_bot
WATCHLIST_FILE  = TRADING_BOT_DIR / "watchlist.json"
MEMORY_FILE     = TRADING_BOT_DIR / "strategy_memory.json"

# ───────────────────────────── data cache ─────────────────────────────
_CACHE: dict = {}
def _cached(key: str, ttl: int, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val

def load_watchlist() -> list[dict]:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return [s for s in json.load(f)["stocks"] if s.get("active", True)]

def save_watchlist_entry(symbol: str, sector: str = "Other", tier: str = "MIDCAP"):
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if any(s["symbol"] == symbol for s in data["stocks"]):
        return False
    data["stocks"].append({"symbol": symbol, "sector": sector, "angel_token": "",
                           "lot_size": 1, "liquidity_tier": tier, "active": True})
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    _CACHE.pop("rs_ranks", None)
    return True

def load_kill_words() -> list[str]:
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            m = json.load(f)
        return [w.lower() for w in m["news_filters"]["always_skip_keywords"]]
    except Exception:
        return ["earnings miss", "below estimate", "below expectations", "weak guidance",
                "guidance cut", "guidance withdrawn", "fpi selling", "margin call",
                "credit watch", "fraud", "default", "downgrade", "sebi probe", "raid"]

def _period_to_days(period: str) -> int:
    """yfinance-style period string -> calendar-day lookback for Angel candles."""
    p = (period or "").strip().lower()
    try:
        if p.endswith("mo"): return int(p[:-2]) * 31 + 7
        if p.endswith("y"):  return int(p[:-1]) * 366 + 7
        if p.endswith("d"):  return int(p[:-1]) + 2
    except Exception:
        pass
    return 400                                            # ~13mo default

def _angel_daily(symbol: str, period: str):
    try:
        return lq.get_candles(symbol, "1d", _period_to_days(period))
    except Exception:
        return None

def history(symbol: str, period: str = "13mo") -> pd.DataFrame:
    """Daily OHLCV. Angel One candles first (real-time, split-UNadjusted); yfinance
    fallback (split/div-adjusted). Same cached interface either way — set
    ANGEL_CANDLES=0 to force yfinance. A short Angel result (<30 bars, e.g. a symbol
    with no angel_token) also falls back so freshly-added tickers still validate."""
    def fetch():
        a = _angel_daily(symbol, period)
        if a is not None and len(a) >= 30:
            return a
        df = yf.Ticker(symbol + ".NS").history(period=period, interval="1d", auto_adjust=True)
        return df.dropna(subset=["Close"])
    return _cached(f"hist:{symbol}:{period}", 600, fetch)

# ───────────────────────────── strategy stats (for EV / Kelly) ─────────────────────────────
# [V10] (S ch9, K ch11): expectancy and Kelly need live win/payoff stats.
# Defaults = F1-hard-veto backtest figures from report 05; overridden by
# strategy_memory.json -> {"live_stats": {"win_rate": .., "payoff_ratio": ..}} when present.
def strategy_stats() -> dict:
    out = {"win_rate": 0.516, "payoff_ratio": 1.5, "source": "backtest_2026-06-10_f1veto"}
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            m = json.load(f)
        ls = m.get("live_stats", {})
        if ls.get("win_rate") and ls.get("payoff_ratio"):
            out = {"win_rate": float(ls["win_rate"]), "payoff_ratio": float(ls["payoff_ratio"]),
                   "source": "strategy_memory.live_stats"}
    except Exception:
        pass
    p, r = out["win_rate"], out["payoff_ratio"]
    out["kelly_f"] = round(max(p - (1 - p) / r, 0.0), 4)          # Kelly: f = p - q/r   (K ch11)
    out["ev_per_R"] = round(p * r - (1 - p), 3)                    # EV in R units        (S ch9)
    out["half_kelly_risk_pct"] = round(out["kelly_f"] * 50, 2)     # half-Kelly, % of capital
    return out

# ───────────────────────────── indicators ─────────────────────────────
def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d  = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([df.High - df.Low,
                    (df.High - df.Close.shift()).abs(),
                    (df.Low  - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """[V10] (K ch6) Wilder ADX — used for strategy routing, not as a gate."""
    up, dn = df.High.diff(), -df.Low.diff()
    plus_dm  = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([df.High - df.Low,
                    (df.High - df.Close.shift()).abs(),
                    (df.Low  - df.Close.shift()).abs()], axis=1).max(axis=1)
    atrn = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atrn
    mdi = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atrn
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

def _ewma_vol(close: pd.Series, lam: float = 0.94) -> pd.Series:
    """[V10] (S ch7) EWMA realized vol, annualized %. RiskMetrics lambda 0.94."""
    r = close.pct_change()
    var = r.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(var * 252) * 100

# ───────────────────────────── market regime ─────────────────────────────
def market_regime() -> dict:
    def fetch():
        nif = yf.Ticker("^NSEI").history(period="13mo", interval="1d").dropna()
        c = nif.Close
        ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
        f1 = bool(c.iloc[-1] > ma50.iloc[-1])
        f2 = bool(c.iloc[-1] > ma200.iloc[-1] and ma200.iloc[-1] > ma200.iloc[-21])
        # [V10] (S ch7/8) VIX in PERCENTILE terms, not absolute level; plus 5d slope
        vix = vix_pctile = vix_slope_5d = None
        try:
            vh = yf.Ticker("^INDIAVIX").history(period="13mo").Close.dropna()
            vix = float(vh.iloc[-1])
            if len(vh) > 60:
                vix_pctile = round(float((vh <= vix).mean() * 100), 1)
                vix_slope_5d = round(float(vix - vh.iloc[-6]), 2)
        except Exception:
            pass
        # [V10] (S ch7) realized vol context for the index
        rv = _ewma_vol(c)
        rv_now = round(float(rv.iloc[-1]), 1) if len(rv.dropna()) else None
        rv_pctile = round(float((rv.dropna() <= rv.iloc[-1]).mean() * 100), 1) if len(rv.dropna()) > 60 else None
        pts = int(f1) + int(f2)
        # tier: percentile-first, absolute fallback (keeps old behaviour if history missing)
        if vix is not None and vix_pctile is not None:
            tier = ("EXTREME" if vix >= 24 or vix_pctile >= 95 else
                    "DANGEROUS" if vix >= 22 or vix_pctile >= 85 else
                    "ELEVATED" if vix >= 18 or vix_pctile >= 70 else "NORMAL")
        else:
            tier = ("EXTREME" if vix and vix >= 24 else "DANGEROUS" if vix and vix >= 22
                    else "ELEVATED" if vix and vix >= 18 else "NORMAL" if vix else "UNKNOWN")
        return {"nifty": round(float(c.iloc[-1]), 1), "f1": f1, "f2": f2,
                "regime_pts": pts, "min_score": {0: 11, 1: 10, 2: 9}[pts],
                "vix": round(vix, 2) if vix else None, "vix_tier": tier,
                "vix_pctile": vix_pctile, "vix_slope_5d": vix_slope_5d,
                "nifty_realized_vol_pct": rv_now, "nifty_rv_pctile": rv_pctile,
                "label": {0: "BEARISH", 1: "NEUTRAL", 2: "BULLISH"}[pts]}
    return _cached("regime", 600, fetch)

def rs_ranks(symbols: list[str] | None = None) -> dict:
    """12-mo return percentile across a universe (default: active watchlist) + vs Nifty.
    Pass `symbols` (e.g. a screener universe) to rank cross-sectionally over THAT set,
    so RS is relative to the scanned universe, not the 55. Cached per-universe.
    [V10] also exposes 12-1 momentum (skip last month — standard academic
    momentum per ML4T ch4: avoids short-term reversal contamination)."""
    syms = symbols if symbols is not None else [s["symbol"] for s in load_watchlist()]
    key = "rs_ranks" if symbols is None else f"rs_ranks:{len(syms)}:{abs(hash(tuple(sorted(syms)))) % 10**8}"
    def fetch():
        rets, rets_12_1, out = {}, {}, {}
        nifty = yf.Ticker("^NSEI").history(period="13mo").Close
        nret = float(nifty.iloc[-1] / nifty.iloc[0] - 1) if len(nifty) > 200 else 0.0
        for sym in syms:
            try:
                df = history(sym)
                if len(df) < 240: continue
                base = df.Close.iloc[-252] if len(df) >= 252 else df.Close.iloc[0]
                rets[sym] = float(df.Close.iloc[-1] / base - 1)
                if len(df) >= 252:
                    rets_12_1[sym] = float(df.Close.iloc[-21] / df.Close.iloc[-252] - 1)
            except Exception:
                pass
        order = sorted(rets, key=rets.get)
        n = max(len(order), 1)
        for i, sym in enumerate(order):
            out[sym] = {"rank": round(i / n * 100, 1),
                        "ret_12m": round(rets[sym] * 100, 1),
                        "rs_vs_nifty": round((rets[sym] - nret) * 100, 1),
                        "mom_12_1": round(rets_12_1.get(sym, rets[sym]) * 100, 1)}
        return out
    return _cached(key, 1800, fetch)

def prefetch_history(symbols: list[str], period: str = "13mo") -> int:
    """Bulk-download daily history for many symbols in ONE threaded yfinance call and
    warm the per-symbol cache — the scale lever for the screener (one batched fetch
    instead of N sequential ones). Best-effort; symbols that fail fall back to the
    normal history() path. Returns count warmed. Note: warms THIS process's cache with
    yfinance bars (the documented signal source); Angel-preferred live paths run in
    their own process so are unaffected."""
    syms = list(dict.fromkeys(symbols))
    if not syms:
        return 0
    try:
        data = yf.download([s + ".NS" for s in syms], period=period, interval="1d",
                           group_by="ticker", auto_adjust=True, threads=True, progress=False, timeout=20)
    except Exception:
        return 0
    now, warmed = time.time(), 0
    for s in syms:
        try:
            df = (data[s + ".NS"] if len(syms) > 1 else data).dropna(subset=["Close"])
            if len(df) >= 30:
                _CACHE[f"hist:{s}:{period}"] = (now, df)
                warmed += 1
        except Exception:
            pass
    return warmed

# ───────────────────────────── optional ML signal (ML4T ch11/12) ─────────────────────────────
def ml_signal_for(symbol: str):
    """[V10] (ML4T) Gradient-boosted probability that the stock beats the
    universe median over the next 10 sessions. None if model/lib not available.
    Train with:  python train_ml.py   (see ml_signal.py)."""
    try:
        from ml_signal import score_universe
        scores = _cached("ml_scores", 1800, score_universe)
        return scores.get(symbol)
    except Exception:
        return None

# ═════════════════════════ AGENT 1 — TECHNICAL ═════════════════════════
def technical_agent(symbol: str, rs_map: dict | None = None) -> dict:
    df = history(symbol)
    if len(df) < 120:
        return {"error": f"insufficient history for {symbol} ({len(df)} bars)"}
    c, v = df.Close, df.Volume
    price = float(c.iloc[-1])
    ma20  = c.rolling(20).mean(); ma50 = c.rolling(50).mean(); ma200 = c.rolling(200).mean()
    rsi   = float(_rsi(c).iloc[-1])
    rsi2  = float(_rsi(c, 2).iloc[-1])                      # [V10] (K ch6/8) short-term MR oscillator
    atr   = float(_atr(df).iloc[-1])
    adx   = float(_adx(df).iloc[-1]) if len(df) >= 30 else None   # [V10] (K ch6)
    vavg  = v.rolling(20).mean()
    obv   = (v * np.sign(c.diff()).fillna(0)).cumsum()
    mid   = c.rolling(20).mean(); sd = c.rolling(20).std()
    hi52  = float(df.High.rolling(min(252, len(df))).max().iloc[-1])
    lo52  = float(df.Low.rolling(min(252, len(df))).min().iloc[-1])
    pr20  = float(((df.High - df.Low) / c).rolling(20).mean().iloc[-1])

    # ----- legacy 14-pt composite (kept as the validated baseline gate) -----
    a1 = 2 if (price > ma50.iloc[-1] and ma20.iloc[-1] > ma50.iloc[-1]) else 0
    a2 = 1 if (ma20.iloc[-1] > ma20.iloc[-6] and ma50.iloc[-1] > ma50.iloc[-21]) else 0
    rk = (rs_map if rs_map is not None else rs_ranks()).get(symbol, {"rank": 50, "ret_12m": None, "rs_vs_nifty": None, "mom_12_1": None})
    b1 = 2 if rk["rank"] >= 75 else 1 if rk["rank"] >= 50 else 0
    b2 = 1 if (hi52 - price) / hi52 <= 0.25 else 0
    b3 = 1 if price >= lo52 * 1.30 else 0
    c1 = 1 if 40 <= rsi <= 65 else 0
    vol_ratio = float(v.iloc[-1] / vavg.iloc[-1]) if vavg.iloc[-1] else 0
    d1 = 1 if vol_ratio >= 1.0 else 0
    d2 = 1 if obv.iloc[-1] > obv.iloc[-11] else 0
    bo = (df.High >= df.High.rolling(20).max()) & (v >= 1.5 * vavg)
    d3 = 1 if bool(bo.iloc[-5:].any()) else 0
    e1 = 1 if 0.015 <= pr20 <= 0.055 else 0
    reg = market_regime()
    score = a1 + a2 + b1 + b2 + b3 + c1 + d1 + d2 + e1 + reg["regime_pts"]

    # ----- [V10] Kestner confirmation filters (K ch8 "new trend filters") -----
    # 1. recency: a 40d high more recent than the 40d low = trend direction confirmed
    w = df.iloc[-40:] if len(df) >= 40 else df
    recency_ok = bool(w.High.idxmax() >= w.Low.idxmin())
    # 2. range expansion: information entering the market (10d range vs 10d range 20d ago)
    range_exp = None
    if len(df) >= 30:
        r_now  = float(df.High.iloc[-10:].max() - df.Low.iloc[-10:].min())
        r_prev = float(df.High.iloc[-30:-20].max() - df.Low.iloc[-30:-20].min())
        range_exp = bool(r_now > r_prev)
    # 3. 12-1 momentum positive (ML4T ch4 standard momentum; decile study favoured momentum)
    mom_12_1_ok = (rk.get("mom_12_1") or 0) > 0
    confirmations = {"recency_40d": recency_ok, "range_expansion": range_exp,
                     "mom_12_1_positive": bool(mom_12_1_ok)}
    n_confirm = sum(1 for x in confirmations.values() if x)

    # ----- [V10] momentum-laggard veto (BT decile study: pure 12-mo momentum
    # top-5 beat the composite; bottom-half momentum names drag) -----
    momentum_veto = rk["rank"] < 40

    # ----- [V10] FIXED mean-reversion sleeve (BT: old rule fired 0x in 92,869
    # stock-days). New rule per report options + Kestner regime table:
    # RSI(2) < 10 (classic short-term MR) + long-term uptrend (>MA200)
    # + ADX < 32 (MR works in non-runaway regimes, K ch6). Paper/shadow first. -----
    ma200_ok = len(df) >= 200 and price > float(ma200.iloc[-1])
    mr = bool(rsi2 < 10 and ma200_ok and (adx is None or adx < 32))
    mr_details = {"rsi2": round(rsi2, 1), "above_ma200": bool(ma200_ok),
                  "adx_ok": (adx is None or adx < 32),
                  "near_lower_bb": bool(price <= (mid.iloc[-1] - 2 * sd.iloc[-1]) * 1.01),
                  "exit_rule": "target = MA20 (mean), time stop 5 days, SL 1.5*ATR",
                  "status": "SHADOW-ONLY until forward-validated"}

    liq = float((c * v).rolling(20).mean().iloc[-1])

    # ----- direction: score gate + F1 HARD VETO (BT: PF 1.16 -> 1.26)
    # + at least 2/3 Kestner confirmations + momentum-laggard veto -----
    direction = "NONE"
    if score >= reg["min_score"] and reg["f1"] and n_confirm >= 2 and not momentum_veto:
        direction = "LONG"

    # ----- [V10] hybrid exit plan (user-selected; K ch8: profit-taking tested
    # worse than trailing, BT: stops bucket -6.3L vs targets +6.8L => compromise:
    # bank half at 3*ATR, trail the rest with a chandelier, BE after the half) -----
    # [V10.1 2026-07-06] stop 2*ATR -> 2.5*ATR: backtest_lab grid, OOS 2024-26 on 55 names
    # PF 1.51->2.02 exp Rs529->809 (94 trades); stop1.5 looked best IS but collapsed OOS (rejected).
    sl  = round(price - 2.5 * atr, 2) if direction == "LONG" else None
    tgt = round(price + 3 * atr, 2) if direction == "LONG" else None
    chandelier = round(float(c.iloc[-22:].max() - 3 * atr), 2) if len(df) >= 22 else None
    exit_plan = None
    if direction == "LONG":
        exit_plan = {
            "initial_stop": sl,
            "target_half": tgt,
            "on_target_half": "sell 50%, move stop on remainder to breakeven",
            "trail_rule": "chandelier = highest close since entry - 3*ATR (update daily)",
            "chandelier_today": chandelier,
            "time_stop": "exit ALL on day 8 if trade never reached +1*ATR; "
                         "winners that banked half are managed by the trail only",
        }

    # ----- [V10] sizing: legacy risk rule capped by half-Kelly (K ch11, S ch9),
    # halved when vol elevated (percentile-based, S ch7/8) -----
    stats = strategy_stats()
    risk_pct = min(4.0, stats["half_kelly_risk_pct"]) if stats["half_kelly_risk_pct"] > 0 else 4.0
    vol_elevated = (reg.get("vix_pctile") or 0) >= 70 or reg["vix_tier"] in ("ELEVATED", "DANGEROUS")
    if vol_elevated:
        risk_pct = round(risk_pct / 2, 2)
    sizing = {"risk_rupees_cap": 20000, "risk_pct_of_capital": risk_pct,
              "kelly_f": stats["kelly_f"], "ev_per_R": stats["ev_per_R"],
              "stats_source": stats["source"],
              "formula": f"qty = min(Rs20k, {risk_pct}% capital) / (2.5*ATR)",
              "vol_adjustment": "halved (vol elevated)" if vol_elevated else "none"}

    ml = ml_signal_for(symbol)   # None when model not trained / lib missing

    buckets = {"trend": a1 + a2, "rs_52w": b1 + b2 + b3, "oscillator": c1,
               "volume": d1 + d2 + d3, "volatility": e1, "regime": reg["regime_pts"]}
    return {
        "symbol": symbol, "price": round(price, 2),
        "score": int(score), "max_score": 14, "min_score_required": reg["min_score"],
        "buckets": buckets, "direction": direction,
        "f1_veto": not reg["f1"],
        "confirmations": confirmations, "n_confirmations": n_confirm,
        "momentum_veto": bool(momentum_veto),
        "atr": round(atr, 2), "atr_pct": round(atr / price * 100, 2),
        "natr_pct": round(atr / price * 100, 2),
        "adx": round(adx, 1) if adx is not None and not math.isnan(adx) else None,
        "entry_zone": [round(price * 0.997, 2), round(price * 1.015, 2)],
        "stop_loss": sl, "target": tgt,
        "exit_plan": exit_plan,
        "rr_ratio": 1.5 if direction == "LONG" else None,
        "sizing": sizing,
        "rsi": round(rsi, 1), "rsi2": round(rsi2, 1), "vol_ratio": round(vol_ratio, 2),
        "ma20": round(float(ma20.iloc[-1]), 2), "ma50": round(float(ma50.iloc[-1]), 2),
        "ma200": round(float(ma200.iloc[-1]), 2) if len(df) >= 200 else None,
        "high_52w": round(hi52, 2), "low_52w": round(lo52, 2),
        "pct_from_52w_high": round((hi52 - price) / hi52 * 100, 1),
        "rs_rank": rk["rank"], "ret_12m_pct": rk["ret_12m"], "rs_vs_nifty_pct": rk["rs_vs_nifty"],
        "mom_12_1_pct": rk.get("mom_12_1"),
        "obv_rising": bool(d2), "breakout_volume": bool(d3),
        "mean_reversion_setup": mr, "mean_reversion_details": mr_details,
        "ml": ml,
        "avg_traded_value_cr": round(liq / 1e7, 1),
        "liquidity_ok": liq >= 5e7,
        "market_regime": reg,
    }

# intraday chart ranges: period key -> (yfinance range, interval). Used only as the
# fallback now — Angel One candles are real-time (no ~15-min Yahoo delay). yfinance NSE
# intraday is delayed ~15 min and limited (5m/15m ~60d back, 1h ~2y).
_INTRADAY = {"5m": ("5d", "5m"), "15m": ("15d", "15m"), "1h": ("90d", "1h")}
# Angel One intraday: calendar-day lookback per period (interval key passed straight through).
_INTRADAY_DAYS = {"5m": 7, "15m": 20, "1h": 100}

def _angel_intraday(symbol: str, period: str):
    try:
        return lq.get_candles(symbol, period, _INTRADAY_DAYS[period])
    except Exception:
        return None

# NSE trades in IST (UTC+5:30, no DST). lightweight-charts plots a UNIX epoch as UTC, so an
# intraday bar would read its UTC time (e.g. 05:15 for a 10:45 IST candle). Shift the epoch by
# +5:30 so the axis shows IST wall-clock. Works for tz-aware (Angel/yfinance) and naive indices.
_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_epoch(d) -> int:
    ts = pd.Timestamp(d)
    if ts.tzinfo is None:
        ts = ts.tz_localize(_IST)            # NSE intraday wall-clock is IST
    return int(ts.timestamp()) + 19800       # +5:30 so the (UTC-based) chart renders IST

def chart_data(symbol: str, period: str = "13mo") -> dict:
    if period in _INTRADAY:
        rng, interval = _INTRADAY[period]
        def fetch():
            a = _angel_intraday(symbol, period)               # Angel first (real-time)
            if a is not None and len(a):
                return a
            d = yf.Ticker(symbol + ".NS").history(period=rng, interval=interval)
            return d.dropna(subset=["Close"])
        df = _cached(f"hist:{symbol}:{period}", 120, fetch)   # 2-min cache for intraday
        t = _ist_epoch                                        # IST wall-clock (chart renders UTC)
    else:
        df = history(symbol, period)
        t = lambda d: d.strftime("%Y-%m-%d")
    c = df.Close
    out = {
        "intraday": period in _INTRADAY,
        "candles": [{"time": t(d), "open": round(float(r.Open), 2),
                     "high": round(float(r.High), 2), "low": round(float(r.Low), 2),
                     "close": round(float(r.Close), 2)} for d, r in df.iterrows()],
        "volume": [{"time": t(d), "value": int(r.Volume),
                    "color": "#26a69a55" if r.Close >= r.Open else "#ef535055"}
                   for d, r in df.iterrows()],
        "ma20":  [{"time": t(d), "value": round(float(x), 2)}
                  for d, x in c.rolling(20).mean().dropna().items()],
        "ma50":  [{"time": t(d), "value": round(float(x), 2)}
                  for d, x in c.rolling(50).mean().dropna().items()],
    }
    return out

# ═════════════════════════ AGENT 2 — FUNDAMENTAL / NEWS ═════════════════════════
# [V10] (ML4T ch14, Loughran-McDonald style) lightweight finance word lists.
_NEG_WORDS = ["miss", "misses", "missed", "weak", "weaker", "decline", "declines", "declined",
              "fall", "falls", "fell", "drop", "drops", "dropped", "plunge", "plunges", "slump",
              "cut", "cuts", "downgrade", "downgraded", "loss", "losses", "probe", "fraud",
              "penalty", "fine", "fined", "lawsuit", "litigation", "default", "delay", "delayed",
              "halt", "halted", "recall", "warning", "warns", "concern", "concerns", "headwind",
              "underperform", "sell-off", "selloff", "bearish", "resign", "resigns", "attrition"]
_POS_WORDS = ["beat", "beats", "strong", "stronger", "growth", "grows", "rise", "rises", "rose",
              "gain", "gains", "surge", "surges", "jump", "jumps", "record", "upgrade", "upgraded",
              "outperform", "buy", "bullish", "profit", "profits", "expansion", "expands", "wins",
              "won", "order", "orders", "contract", "approval", "approved", "launch", "launches",
              "dividend", "buyback", "tailwind", "raised", "raises", "guidance raise"]

def _news_score(headlines: list[dict]) -> dict:
    pos = neg = 0
    for h in headlines:
        low = h["title"].lower()
        pos += sum(1 for w in _POS_WORDS if w in low)
        neg += sum(1 for w in _NEG_WORDS if w in low)
    total = pos + neg
    score = round((pos - neg) / total, 2) if total else 0.0
    return {"pos_hits": pos, "neg_hits": neg, "net_score": score}   # net_score in [-1, 1]

# Per-stock news recency window. Default 48h (covers weekends/gaps); set NEWS_MAX_AGE_HOURS=24
# for a strict 1-day window. Items with no parseable timestamp are treated as stale and dropped.
NEWS_MAX_AGE_HOURS = int(os.getenv("NEWS_MAX_AGE_HOURS", "48"))

def _news_dt(it: dict, content: dict):
    """Robust publish time for a yfinance news item -> tz-aware UTC datetime, or None if
    undated. Handles the flat epoch `providerPublishTime` AND the newer nested
    content.pubDate / content.displayTime (ISO). None => treated as stale and dropped."""
    ts = it.get("providerPublishTime")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), timezone.utc)
        except Exception:
            pass
    for k in ("pubDate", "displayTime", "date"):
        v = content.get(k)
        if v:
            try:
                d = pd.Timestamp(v)
                d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
                return d.to_pydatetime()
            except Exception:
                pass
    return None

def fundamental_agent(symbol: str) -> dict:
    kill_words = load_kill_words()
    headlines, flagged = [], []

    # yfinance news — STRICT recency: only items with a REAL timestamp inside the window.
    # Undated items are dropped (they were leaking stale articles via `not ts`). DuckDuckGo
    # is intentionally NOT used here: it returns untimestamped results, so it can't satisfy a
    # "last 24-48h + show the time" requirement. Each kept headline carries its publish time.
    now = datetime.now(timezone.utc)
    cutoff_ts = now.timestamp() - NEWS_MAX_AGE_HOURS * 3600
    try:
        items = yf.Ticker(symbol + ".NS").news or []
        for it in items[:25]:
            content = it.get("content", it)
            title = (content.get("title") or it.get("title") or "").strip()
            dt = _news_dt(it, content)
            if not title or dt is None or dt.timestamp() < cutoff_ts:
                continue                                   # no title / undated / stale -> drop
            pub = (content.get("provider") or {}).get("displayName") or it.get("publisher") or "yahoo"
            headlines.append({"src": pub, "title": title,
                              "ts": dt.isoformat(timespec="minutes"),
                              "age_h": round((now.timestamp() - dt.timestamp()) / 3600, 1)})
    except Exception:
        pass
    headlines.sort(key=lambda h: h.get("ts", ""), reverse=True)   # newest first

    for h in headlines:
        low = h["title"].lower()
        hits = [w for w in kill_words if w in low]
        if hits:
            flagged.append({"title": h["title"], "kill_words": hits})

    # earnings proximity (approximation — yfinance calendar)
    # [V10] (S ch7): earnings are binary vol events; widen the no-trade window
    # to T-5..T+1 and add a 10-day caution flag.
    earnings_in_days = None
    try:
        cal = yf.Ticker(symbol + ".NS").calendar
        ed = None
        if isinstance(cal, dict):
            d_ = cal.get("Earnings Date")
            if isinstance(d_, list) and d_: ed = d_[0]
            elif d_ is not None: ed = d_
        if ed is not None:
            ed = pd.Timestamp(ed).date()
            earnings_in_days = (ed - date.today()).days
    except Exception:
        pass
    earnings_risk = earnings_in_days is not None and -1 <= earnings_in_days <= 5
    earnings_caution = earnings_in_days is not None and 5 < earnings_in_days <= 10

    # quality snapshot (best effort; yfinance .info is approximate)
    quality = {}
    try:
        info = yf.Ticker(symbol + ".NS").info or {}
        quality = {
            "roe_pct": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") else None,
            "debt_to_equity": round(info["debtToEquity"] / 100, 2) if info.get("debtToEquity") else None,
            "pe": round(info["trailingPE"], 1) if info.get("trailingPE") else None,
            "profit_margin_pct": round(info["profitMargins"] * 100, 1) if info.get("profitMargins") else None,
            "market_cap_cr": round(info["marketCap"] / 1e7, 0) if info.get("marketCap") else None,
            "rev_growth_pct": round(info["revenueGrowth"] * 100, 1) if info.get("revenueGrowth") else None,
        }
    except Exception:
        pass
    # [V10] (ML4T ch4 quality factors) 0-4 quality score — tiebreaker, not a gate
    qs = 0
    if (quality.get("roe_pct") or 0) >= 15: qs += 1
    if quality.get("debt_to_equity") is not None and quality["debt_to_equity"] <= 1.0: qs += 1
    if (quality.get("profit_margin_pct") or 0) >= 8: qs += 1
    if (quality.get("rev_growth_pct") or 0) > 0: qs += 1

    senti = _news_score(headlines)
    sentiment = ("NEGATIVE" if flagged or senti["net_score"] <= -0.5
                 else "POSITIVE" if senti["net_score"] >= 0.5 and headlines
                 else "NEUTRAL" if headlines else "NO_NEWS")
    return {"symbol": symbol, "headlines": headlines[:8], "kill_word_hits": flagged,
            "earnings_in_days": earnings_in_days,
            "earnings_risk": earnings_risk, "earnings_caution": earnings_caution,
            "quality": quality, "quality_score": qs, "news_sentiment": sentiment,
            "news_score": senti, "news_window_hours": NEWS_MAX_AGE_HOURS}

# ═════════════════════════ AGENT 3 — STRATEGIST (Claude) ═════════════════════════
# [V10] EV-first playbook (Sinclair ch9), Cohen direction x volatility matrix,
# hybrid exits, percentile-based vol regime, ML floor. Claude can veto/downgrade only.
PLAYBOOK_RULES = """REGIME GATES EVERYTHING. f1 false (Nifty<50dma) => HARD VETO on new longs: action WAIT or SELL only.
VIX EXTREME (>=24 or pctile>=95) => no new entries. ELEVATED/DANGEROUS => conviction and size halved, defined-risk only.
EQUITY SIGNAL: direction LONG (score>=min_score AND f1 AND >=2/3 confirmations AND rs_rank>=40) => candidate BUY.
EXPECTED VALUE: think in EV per rupee risked (ev_per_R supplied). A trade with negative EV is never a BUY regardless of conviction.
WIN-RATE IS NOT EDGE: never justify a trade by probability of winning alone; cite EV (Sinclair).
EXITS given by Python (hybrid): initial stop 2.5*ATR; bank HALF at +3*ATR and move stop to breakeven;
trail remainder with chandelier (highest close since entry - 3*ATR); day-8 time stop only if trade never reached +1*ATR.
Never invent numbers; repeat Python's exit_plan verbatim in your answer.
NEWS: any kill_word_hits => veto (WAIT/AVOID). earnings_risk true (T-5..T+1) => WAIT. earnings_caution => mention, size down.
PRE-MARKET NEWS BRAIN (Agent 6, 24h synthesis): the `news` block carries market_bias, this stock's sector_bias/confidence, and any direct stock_flag — each sourced from real headlines that morning.
  - stock_flag.bias NEGATIVE => kill-word-grade veto: action WAIT (never buy into a fresh negative company catalyst).
  - sector_bias NEGATIVE with sector_confidence>=0.6 (e.g. an Accenture guide-down => NSE IT contagion) => WAIT on new longs in that sector today.
  - market_bias RISK_OFF => more conservative, size down, defined-risk only (do not double-count if F1/VIX already gated).
  - POSITIVE news may support conviction but NEVER upgrades a below-threshold technical signal to BUY.
  - Cite the specific news headline in reasons_for/against. News may only veto/downgrade, never originate a trade.
MEAN-REVERSION setups (rsi2<10, >MA200, ADX<32) are SHADOW-ONLY: report them, never action BUY from them.
LIQUIDITY: liquidity_ok false => AVOID (cannot exit cleanly).
ML SIGNAL: ml.prob is a 10-day cross-sectional probability. >0.60 supports conviction; <0.40 forces WAIT; absent => ignore silently.
OPTIONS OVERLAY (suggestion only, NEVER naked; Cohen direction x volatility matrix, VIX percentile based):
bullish + vix_pctile<30  => ATM_CALL or BULL_CALL_SPREAD (premium cheap, long vega acceptable);
bullish + vix_pctile 30-70 => BULL_CALL_SPREAD (debit, defined risk);
bullish + vix_pctile>70  => BULL_PUT_SPREAD credit (sell rich premium, defined risk) - never naked puts;
neutral + vix_pctile>70 AND vix_slope_5d<=0 => IRON_CONDOR zone (paper only, skip event weeks);
any short-premium idea when vix_slope_5d>0 (vol rising) => forbidden (Sinclair: never short vol into rising vol).
Weekly expiry pin strategies (the +-200pt fly) are RETIRED: own study shows 48% hit rate, not ~80%.
Stock options must exit before expiry week (physical delivery). Index spreads preferred for liquidity.
HONESTY: always give reasons_against (>=2), conviction 0-1, what_invalidates, and the EV line.
You may VETO or downgrade. You may NEVER upgrade below-threshold signals to BUY, never change Python's numbers."""

def strategist_agent(tech: dict, fund: dict, news_ctx: dict | None = None) -> dict:
    """ONE Claude call. Strict JSON. Falls back to deterministic rules if no API key.
    news_ctx = Agent 6 pre-market briefing slice for this symbol (sector/stock bias)."""
    payload = {
        "technical": {k: tech[k] for k in
                      ("symbol", "price", "score", "max_score", "min_score_required", "buckets",
                       "direction", "f1_veto", "confirmations", "n_confirmations", "momentum_veto",
                       "atr_pct", "adx", "stop_loss", "target", "exit_plan", "entry_zone",
                       "rsi", "rsi2", "vol_ratio", "rs_rank", "rs_vs_nifty_pct", "mom_12_1_pct",
                       "pct_from_52w_high", "obv_rising", "breakout_volume",
                       "mean_reversion_setup", "mean_reversion_details",
                       "sizing", "ml", "liquidity_ok", "avg_traded_value_cr") if k in tech},
        "regime": tech.get("market_regime"),
        "fundamental": {k: fund[k] for k in
                        ("news_sentiment", "news_score", "kill_word_hits", "earnings_in_days",
                         "earnings_risk", "earnings_caution", "quality", "quality_score") if k in fund},
        "recent_headlines": [h["title"] for h in fund.get("headlines", [])][:6],
        "news": news_ctx,                          # Agent 6 pre-market brain (None if no briefing)
    }
    schema = {
        "action": "BUY | SELL_AVOID | WAIT",
        "instrument": "EQUITY | BULL_CALL_SPREAD | BULL_PUT_SPREAD | ATM_CALL | IRON_CONDOR | NONE",
        "conviction": 0.0,
        "expected_value_note": "EV per R risked, from supplied stats",
        "entry_zone": [0, 0], "stop_loss": 0, "target": 0,
        "exit_plan": "repeat Python exit_plan",
        "holding_period": "5-12 trading days (trail can extend winners)",
        "position_size_note": "min(Rs20k, half-Kelly capped pct) / (2.5 x ATR)",
        "reasons_for": ["..."], "reasons_against": ["..."],
        "what_invalidates": "...", "summary": "<=50 words plain-English blueprint",
    }
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            # bounded latency: a network blip must fail in ~1 min, not hang 10+ min on
            # the SDK's default timeout/retries (caught by the universe-cycle re-check).
            client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=1)
            msg = client.messages.create(
                model=os.getenv("STRATEGIST_MODEL", "claude-sonnet-4-6"),
                max_tokens=1000,
                system=("You are the strategist of an NSE swing-trading desk. Apply the rules EXACTLY. "
                        "Output ONLY a JSON object matching the schema. Use ONLY numbers supplied in input.\n\nRULES:\n"
                        + PLAYBOOK_RULES),
                messages=[{"role": "user", "content":
                           f"INPUT:\n{json.dumps(payload, indent=1)}\n\nSCHEMA:\n{json.dumps(schema, indent=1)}\n\nReturn ONLY the JSON object."}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            out = json.loads(raw.strip())
            out["engine"] = "claude"
            out["tokens"] = {"in": msg.usage.input_tokens, "out": msg.usage.output_tokens}
            return _sanitize(out, tech, fund, news_ctx)
        except Exception as e:
            fallback = _rules_only(tech, fund, news_ctx)
            fallback["engine"] = f"rules-fallback (claude error: {str(e)[:80]})"
            return fallback
    out = _rules_only(tech, fund, news_ctx)
    out["engine"] = "rules-only (no ANTHROPIC_API_KEY)"
    return out

def _news_veto_reason(news_ctx: dict | None) -> str | None:
    """Agent 6 hard floor: a fresh negative catalyst (direct stock flag, or high-confidence
    NEGATIVE sector contagion like Accenture=>NSE IT) vetoes a new long today. Returns the
    reason string to veto on, or None. News may only veto/downgrade — never originate."""
    if not news_ctx:
        return None
    sf = news_ctx.get("stock_flag") or {}
    if sf.get("bias") == "NEGATIVE":
        return f"News Brain negative catalyst on this stock - {(sf.get('reason') or '')[:90]}"
    if news_ctx.get("sector_bias") == "NEGATIVE" and (news_ctx.get("sector_confidence") or 0) >= 0.6:
        return (f"News Brain: {news_ctx.get('sector')} sector NEGATIVE "
                f"(conf {news_ctx.get('sector_confidence')}) - {(news_ctx.get('sector_reason') or '')[:70]}")
    return None


def _sanitize(out: dict, tech: dict, fund: dict, news_ctx: dict | None = None) -> dict:
    """Claude can veto/downgrade but never override Python's numbers or upgrade signals.
    [V10] extra hard floors: earnings window, VIX extreme, ML floor, defined-risk coercion.
    [V11] News Brain (Agent 6): negative pre-market news/contagion vetoes new longs."""
    reg = tech.get("market_regime", {})
    ml = tech.get("ml") or {}
    def _veto(reason):
        out["action"] = "WAIT"
        out.setdefault("reasons_against", []).insert(0, f"Sanitizer: {reason}")
    if out.get("action") == "BUY":
        if tech.get("direction") != "LONG":
            _veto("signal below threshold / vetoed by gates - BUY downgraded to WAIT")
        elif fund.get("kill_word_hits"):
            _veto("kill-word news present - BUY vetoed")
        elif _news_veto_reason(news_ctx):
            _veto(_news_veto_reason(news_ctx))
        elif fund.get("earnings_risk"):
            _veto(f"earnings in {fund.get('earnings_in_days')} day(s) - binary event window")
        elif reg.get("vix_tier") == "EXTREME":
            _veto(f"VIX {reg.get('vix')} extreme - entries halted")
        elif ml.get("prob") is not None and ml["prob"] < 0.40:
            _veto(f"ML probability {ml['prob']:.2f} < 0.40 floor")
    # short-premium structures forbidden when vol is rising
    if out.get("instrument") in ("BULL_PUT_SPREAD", "IRON_CONDOR") and (reg.get("vix_slope_5d") or 0) > 0:
        out["instrument"] = "EQUITY" if out.get("action") == "BUY" else "NONE"
        out.setdefault("reasons_against", []).insert(0, "Sanitizer: short premium blocked while VIX rising")
    if tech.get("stop_loss"):
        out["stop_loss"] = tech["stop_loss"]; out["target"] = tech["target"]
        out["entry_zone"] = tech["entry_zone"]
    out["exit_plan"] = tech.get("exit_plan")
    if out.get("action") != "BUY":          # no levels on WAIT/AVOID - avoid 0-placeholders
        out["stop_loss"] = tech.get("stop_loss"); out["target"] = tech.get("target")
        out["instrument"] = "NONE"
    return out

def _rules_only(tech: dict, fund: dict, news_ctx: dict | None = None) -> dict:
    """Deterministic fallback strategist — the V10 playbook as code."""
    reg = tech["market_regime"]; rf, ra = [], []
    stats = strategy_stats()
    ml = tech.get("ml") or {}
    action = "WAIT"
    if not tech.get("liquidity_ok", True):
        action = "SELL_AVOID"; ra.append(f"Liquidity {tech['avg_traded_value_cr']}cr/day < 5cr gate")
    elif fund.get("kill_word_hits"):
        action = "SELL_AVOID"; ra.append("Kill-word news: " + fund["kill_word_hits"][0]["title"][:80])
    elif _news_veto_reason(news_ctx):
        action = "WAIT"; ra.append(_news_veto_reason(news_ctx))
    elif fund.get("earnings_risk"):
        action = "WAIT"; ra.append(f"Earnings in {fund['earnings_in_days']} day(s) - binary event window (T-5..T+1)")
    elif tech["f1_veto"]:
        action = "WAIT"; ra.append("Nifty below 50dma - F1 HARD veto on new longs")
    elif reg.get("vix_tier") == "EXTREME":
        action = "WAIT"; ra.append(f"India VIX {reg['vix']} extreme - entries halted")
    elif tech.get("momentum_veto"):
        action = "WAIT"; ra.append(f"RS rank {tech['rs_rank']} < 40 - momentum laggard (decile study)")
    elif tech.get("n_confirmations", 0) < 2 and tech["direction"] != "LONG" and tech["score"] >= tech["min_score_required"]:
        action = "WAIT"; ra.append(f"Only {tech.get('n_confirmations', 0)}/3 Kestner confirmations - trend not validated")
    elif ml.get("prob") is not None and ml["prob"] < 0.40:
        action = "WAIT"; ra.append(f"ML probability {ml['prob']:.2f} below 0.40 floor")
    elif tech["direction"] == "LONG":
        action = "BUY"
        rf.append(f"Score {tech['score']}/14 >= {tech['min_score_required']} in {reg['label']} regime, "
                  f"{tech['n_confirmations']}/3 confirmations")
        rf.append(f"EV {stats['ev_per_R']}R per trade at p={stats['win_rate']}, payoff {stats['payoff_ratio']} ({stats['source']})")
        if tech["buckets"]["rs_52w"] >= 3: rf.append(f"Relative strength leader (RS rank {tech['rs_rank']}, {tech['pct_from_52w_high']}% from 52w high)")
        if tech["breakout_volume"]: rf.append("Breakout candle with >=1.5x volume in last 5 sessions")
        if tech["obv_rising"]: rf.append("OBV rising - accumulation confirmed")
        if ml.get("prob") is not None and ml["prob"] >= 0.60: rf.append(f"ML probability {ml['prob']:.2f} (top of universe)")
        if fund.get("quality_score", 0) >= 3: rf.append(f"Quality score {fund['quality_score']}/4")
    else:
        ra.append(f"Score {tech['score']}/14 below {tech['min_score_required']} threshold - no edge")
    if action == "BUY":
        ra.append(f"Costs ~0.3% round trip eat thin edges; ATR {tech['atr_pct']}% gap risk overnight")
        if reg.get("vix_pctile") and reg["vix_pctile"] >= 70: ra.append(f"VIX percentile {reg['vix_pctile']} - size halved")
        if (tech.get("rs_vs_nifty_pct") or 0) < 0: ra.append("12-mo return trails Nifty - momentum not market-leading")
        if fund.get("earnings_caution"): ra.append(f"Earnings in {fund['earnings_in_days']} days - exit or size down before window")
        if ml.get("prob") is not None and 0.40 <= ml["prob"] < 0.50: ra.append(f"ML probability {ml['prob']:.2f} lukewarm")
        if news_ctx and news_ctx.get("market_bias") == "RISK_OFF":
            ra.append(f"News Brain market_bias RISK_OFF - {(news_ctx.get('market_bias_reason') or '')[:60]}")
        if news_ctx and news_ctx.get("sector_bias") == "POSITIVE":
            rf.append(f"News Brain: {news_ctx.get('sector')} sector POSITIVE - {(news_ctx.get('sector_reason') or '')[:60]}")
    # [V10] options instrument by Cohen matrix, VIX percentile based
    instrument = "NONE"
    if action == "BUY":
        vp = reg.get("vix_pctile")
        slope = reg.get("vix_slope_5d") or 0
        if vp is None:
            vix = reg.get("vix") or 15
            instrument = "ATM_CALL" if vix < 13 else "BULL_CALL_SPREAD" if vix < 18 else "EQUITY"
        elif vp < 30:
            instrument = "BULL_CALL_SPREAD" if (reg.get("vix") or 14) >= 13 else "ATM_CALL"
        elif vp <= 70:
            instrument = "BULL_CALL_SPREAD"
        else:
            instrument = "BULL_PUT_SPREAD" if slope <= 0 else "EQUITY"
        if instrument != "EQUITY":
            ra.append("Options overlay is paper-only until real option-chain pricing is wired in")
    conviction = 0.0
    if action == "BUY":
        conviction = 0.45 + 0.06 * (tech["score"] - tech["min_score_required"])
        conviction += 0.05 * max(tech.get("n_confirmations", 0) - 2, 0)
        conviction += 0.1 if tech["breakout_volume"] else 0
        if ml.get("prob") is not None:
            conviction += 0.15 * (ml["prob"] - 0.5) * 2          # +-0.15 tilt
        if (reg.get("vix_pctile") or 0) >= 70 or reg["vix_tier"] in ("ELEVATED", "DANGEROUS"):
            conviction *= 0.5
        conviction = min(max(round(conviction, 2), 0.05), 0.9)
    sz = tech.get("sizing", {})
    return {"action": action, "instrument": instrument if action == "BUY" else "NONE",
            "conviction": conviction,
            "expected_value_note": f"EV = {stats['ev_per_R']}R per rupee risked "
                                   f"(p={stats['win_rate']}, payoff={stats['payoff_ratio']}, {stats['source']})",
            "entry_zone": tech["entry_zone"], "stop_loss": tech["stop_loss"], "target": tech["target"],
            "exit_plan": tech.get("exit_plan"),
            "holding_period": "5-12 trading days; chandelier trail can extend winners",
            "position_size_note": sz.get("formula", "qty = min(Rs20,000, 4% capital) / (2.5 x ATR)")
                                  + (f" [{sz.get('vol_adjustment')}]" if sz.get("vol_adjustment") not in (None, "none") else ""),
            "reasons_for": rf or ["-"], "reasons_against": ra or ["-"],
            "what_invalidates": (f"Close below Rs{tech['stop_loss']} (2.5xATR stop), Nifty closing below its 50dma, "
                                 f"any kill-word headline, or ML prob dropping under 0.40"
                                 if action == "BUY" else
                                 "A close above threshold score with f1 true, >=2/3 confirmations and RS rank >=40"),
            "summary": (f"{tech['symbol']}: {action}. Score {tech['score']}/14 vs need {tech['min_score_required']}, "
                        f"{tech.get('n_confirmations', 0)}/3 confirmations, regime {reg['label']}, "
                        f"VIX {reg.get('vix')} (pct {reg.get('vix_pctile')}). "
                        + ("Enter in zone, stop 2.5xATR, bank half at 3xATR then trail; exit day 8 only if never +1xATR."
                           if action == "BUY" else "No edge right now - preserve capital."))}

# ───────────────────────────── Agent 6 briefing access (pre-market News Brain) ─────────────────────────────
def _today_briefing():
    """Cached load of today's pre-market briefing (None if missing/stale)."""
    def fetch():
        try:
            import news_brain
            return news_brain.load_today_briefing()
        except Exception:
            return None
    return _cached("news_brief", 300, fetch)

def _symbol_sector(symbol: str):
    def fetch():
        return {s["symbol"]: s.get("sector") for s in load_watchlist()}
    return _cached("sym_sector_map", 1800, fetch).get(symbol)

def recommend(symbol: str) -> dict:
    tech = technical_agent(symbol)
    if "error" in tech:
        return tech
    fund = fundamental_agent(symbol)
    news_ctx = None
    brief = _today_briefing()
    if brief:
        try:
            import news_brain
            news_ctx = news_brain.news_context_for(symbol, _symbol_sector(symbol), brief)
        except Exception:
            news_ctx = None
    rec = strategist_agent(tech, fund, news_ctx)
    return {"symbol": symbol, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "technical": tech, "fundamental": fund,
            "news_context": news_ctx, "recommendation": rec}
