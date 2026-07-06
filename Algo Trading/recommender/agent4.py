"""
AGENT 4 — Trade Executor / Position Manager (PAPER TRADING ONLY).

Works like a desk trader who runs purely on maths:
  • pulls Agent 1-3 recommendations, decides which trades to take
  • sizes positions off risk (never off feelings), enforces portfolio limits
  • manages every open position daily: stop, half-bank at 3*ATR, breakeven,
    chandelier trail, day-12 time stop, options expiry exit
  • kill switches: daily-loss halt, max-drawdown halt, regime halt (F1 / VIX)
  • logs EVERY decision (taken or skipped) with a full feature snapshot to
    agent4_decisions.jsonl — this becomes the ML validation dataset.

Capital: Rs 20,00,000 paper. No real orders are ever placed.

Risk rules (all % of CURRENT equity, not start capital):
  RISK_PCT            1.0%  risk per trade (halved when VIX elevated, via Agent 1)
  MAX_POSITIONS       6     concurrent open positions
  MAX_POS_VALUE_PCT   20%   single position exposure cap (equity legs)
  MAX_HEAT_PCT        5%    sum of open risk across all positions
  DAILY_LOSS_HALT     2%    realized day loss -> no new entries until next day
  MAX_DD_HALT         10%   equity below peak*0.90 -> halt, manual reset required

Options (paper, defined-risk only — never naked):
  Taken only when the strategist proposes ATM_CALL / BULL_CALL_SPREAD /
  BULL_PUT_SPREAD, conviction >= OPT_CONVICTION_MIN, real option-chain premiums
  are available from NSE, and the watchlist has a real lot size. Otherwise the
  trade falls back to EQUITY and the fallback is logged. Position size is set so
  the structure's MAXIMUM loss fits inside the trade risk budget.

State:      C:\\trading_bot\\agent4_state.json      (portfolio, positions, closed)
Decisions:  C:\\trading_bot\\agent4_decisions.jsonl (one JSON line per decision)

CLI:  python agent4.py execute   -> entry cycle (scan -> recommend -> enter)
      python agent4.py monitor   -> manage open positions
      python agent4.py summary   -> print portfolio summary
"""
from __future__ import annotations
import os, sys, json, math, time, tempfile
from pathlib import Path
from datetime import datetime, date, timedelta
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

import agents
import universe as uni
try:
    import live_quotes as lq          # Angel One live LTP/OHLC, yfinance fallback
except Exception:
    lq = None
try:
    from notify import notify         # alerts.log + optional Telegram
except Exception:
    def notify(msg, level="WARN"):    # degrade silently in test harnesses
        return {}

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
STATE_FILE      = TRADING_BOT_DIR / "agent4_state.json"
DECISIONS_FILE  = TRADING_BOT_DIR / "agent4_decisions.jsonl"

# load .env so the CLI (scheduled tasks) gets ANTHROPIC_API_KEY for Agent 3,
# exactly like app.py does. Without this, scheduled runs fall back to rules-only.
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=False)
except Exception:
    pass

sys.path.insert(0, str(TRADING_BOT_DIR))
try:
    from cost_model import transaction_cost          # the audited NSE cost model
except Exception:                                    # pragma: no cover
    def transaction_cost(segment, side, price, quantity, lot_size=1):
        return round(0.0015 * price * quantity * max(lot_size, 1), 2)  # crude fallback

# ───────────────────────────── configuration ─────────────────────────────
START_CAPITAL       = 2_000_000.0  # Rs 20 lakh paper capital
RISK_PCT            = 1.0          # % of equity risked per trade (upper bound)
RISK_RUPEE_CAP      = 20_000.0     # absolute cap per trade (matches Agent 1; = 1% of 20L)
# [V10.1 2026-07-06, user decision] "trade as much as the logic allows": positions 6->12,
# heat 5->8%, option slots 3->5 (all env-tunable). Backtest support: maxpos5 on 5L held OOS
# (PF 1.94, 145 trades) — more slots = more diversification at slight PF cost. NOTE the true
# throttle is MAX_HEAT_PCT: at Rs20k risk/trade, 8% of 20L = Rs1.6L open risk = ~8 full-risk
# positions concurrently. Kill switches (2% day loss / 10% drawdown) unchanged and still rule.
MAX_POSITIONS       = int(os.getenv("AGENT4_MAX_POSITIONS", "12"))
MAX_POS_VALUE_PCT   = 20.0
MAX_HEAT_PCT        = float(os.getenv("AGENT4_MAX_HEAT_PCT", "8.0"))
DAILY_LOSS_HALT_PCT = 2.0
MAX_DD_HALT_PCT     = 10.0
CONVICTION_MIN      = 0.35         # equity entries
OPT_CONVICTION_MIN  = 0.55         # options entries (higher bar — deliberate risk choice)
MAX_OPTION_POS      = int(os.getenv("AGENT4_MAX_OPTION_POS", "5"))
SLIPPAGE_EQ_PCT     = 0.10         # paper fill slippage, equity (% of price)
SLIPPAGE_OPT_PCT    = 1.00         # paper fill slippage, INDEX options (% of premium)
SLIPPAGE_OPT_STOCK  = 2.00         # stock options: wider books, 2% honest slippage
MAX_LEG_SPREAD_PCT  = 5.0          # stock option legs: reject if bid-ask spread > 5% of mid
ENTRY_ZONE_TOL      = 0.005        # live price may exceed entry zone top by max 0.5%
TIME_STOP_SESSIONS  = 8            # exit if never reached +1*ATR by session 8
                                   # [V10.1 2026-07-06] 12->8: backtest_lab OOS PF 1.51->1.58, maxDD halved
OPT_EXIT_DTE        = 4            # close options this many calendar days before expiry
TOP_N_RECS          = int(os.getenv("AGENT4_TOP_N_RECS", "5"))   # max Claude recs/cycle — SPEED LEVER (lower = faster)
# Universe Agent 4 scans for NEW entries. core = curated 55; nifty210 = Nifty 50+Bank+Midcap150
# (~204); nifty500 = full. Only the top TOP_N_RECS by score get a Claude call, so cost is
# bounded regardless of universe size. New names route EQUITY-only (no verified F&O lot).
ENTRY_UNIVERSE_SCOPE = os.getenv("AGENT4_UNIVERSE_SCOPE", "nifty210")

# ----- index sleeve (NIFTY / BANKNIFTY via NSE option chain; defined-risk only) -----
# Lot sizes per NSE circular effective Jan-2026 series (NIFTY 75->65, BANKNIFTY 35->30).
# VERIFY against the latest NSE circular if entries look mis-sized — lots change by circular.
# SENSEX is NOT here: its options trade on BSE, which this NSE chain fetcher cannot reach.
INDEX_UNIVERSE = [
    {"symbol": "NIFTY",     "yahoo": "^NSEI",    "lot_size": 65},
    {"symbol": "BANKNIFTY", "yahoo": "^NSEBANK", "lot_size": 30},
]
INDEX_MIN_SCORE     = 5            # of 7-point index trend score, below = no trade
INDEX_CONVICTION    = 0.60         # fixed conviction tag for index spread entries

_LOCK = Lock()

# ───────────────────────────── state I/O ─────────────────────────────
def _blank_state() -> dict:
    return {"created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "start_capital": START_CAPITAL, "cash": START_CAPITAL,
            "peak_equity": START_CAPITAL,
            "halted": False, "halt_reason": None,
            "day": {"date": str(date.today()), "realized_pnl": 0.0},
            "next_id": 1, "positions": [], "closed": [], "equity_curve": []}

def _load() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        # capital migration: if START_CAPITAL changed and the book is still
        # untouched (no trades, no positions), restart clean at the new size.
        # A book WITH history is never rewritten — that would falsify the record.
        if (state.get("start_capital") != START_CAPITAL
                and not state.get("positions") and not state.get("closed")):
            state = _blank_state()
        return state
    return _blank_state()

def _save(state: dict):
    # atomic write — never leave a half-written portfolio on disk
    fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)

def _log_decision(rec: dict):
    rec["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

HEARTBEAT_FILE = TRADING_BOT_DIR / "agent4_heartbeat.json"

def _heartbeat(kind: str, **info):
    """Each run stamps proof-of-life; the watchdog checks it at 16:05."""
    try:
        hb = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8")) if HEARTBEAT_FILE.exists() else {}
    except Exception:
        hb = {}
    today = str(date.today())
    if kind == "monitor":
        prev = hb.get("monitor", {})
        info["runs_today"] = (prev.get("runs_today", 0) + 1) if prev.get("date") == today else 1
    hb[kind] = {"date": today, "ts": datetime.now().strftime("%H:%M:%S"), **info}
    try:
        HEARTBEAT_FILE.write_text(json.dumps(hb, indent=1), encoding="utf-8")
    except Exception:
        pass

def _roll_day(state: dict):
    today = str(date.today())
    if state["day"]["date"] != today:
        state["day"] = {"date": today, "realized_pnl": 0.0}
        if state.get("halt_reason") == "DAILY_LOSS":      # daily halt auto-resets
            state["halted"], state["halt_reason"] = False, None

# ───────────────────────────── market data helpers ─────────────────────────────
def _last_bar(symbol: str):
    """Latest daily OHLC for a symbol (cached via agents.history)."""
    df = agents.history(symbol, "6mo")
    if df is None or not len(df):
        return None
    r = df.iloc[-1]
    return {"date": df.index[-1].strftime("%Y-%m-%d"),
            "open": float(r.Open), "high": float(r.High),
            "low": float(r.Low), "close": float(r.Close)}

def _index_history(yahoo: str):
    """Daily OHLC df for an index (^NSEI etc.) — same cache as stock data.
    Kept separate from agents.history() because that appends '.NS'."""
    import yfinance as yf
    def fetch():
        return yf.Ticker(yahoo).history(period="13mo", interval="1d").dropna(subset=["Close"])
    return agents._cached(f"idx:{yahoo}", 600, fetch)

def _bar(pos: dict):
    """Latest bar for any position type. Prefers TODAY'S LIVE bar (Angel One:
    running open/high/low + LTP as close) so 12:30 stop checks see intraday
    truth, not yesterday's candle. Falls back to delayed daily data."""
    if lq:
        try:
            q = lq.get_quote(pos["symbol"])
            if q and q.get("date") == str(date.today()):
                return {"date": q["date"], "open": q["open"], "high": q["high"],
                        "low": q["low"], "close": q["ltp"], "source": q["source"]}
        except Exception:
            pass
    if pos.get("yahoo"):
        df = _index_history(pos["yahoo"])
        if df is None or not len(df):
            return None
        r = df.iloc[-1]
        return {"date": df.index[-1].strftime("%Y-%m-%d"),
                "open": float(r.Open), "high": float(r.High),
                "low": float(r.Low), "close": float(r.Close)}
    return _last_bar(pos["symbol"])

def _lot_size(symbol: str) -> int:
    try:
        for s in agents.load_watchlist():
            if s["symbol"] == symbol:
                ls = int(s.get("lot_size") or 1)
                if ls > 1:
                    return ls                              # curated F&O lot wins
                break
    except Exception:
        pass
    try:                                                   # expanded-universe F&O names: Angel master
        import live_quotes as lq
        return int(lq.option_lot_sizes().get(symbol.upper(), 1)) or 1
    except Exception:
        return 1

# ───────────────────────────── NSE option chain (free, best-effort) ─────────────────────────────
_NSE_CACHE: dict = {}
_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

def _chain_from_nse(symbol: str) -> dict | None:
    """Fallback: the free (unofficial, cookie-gated, often-blocked) NSE option-chain endpoint."""
    try:
        import requests
        kind = "indices" if symbol in _INDEX_SYMBOLS else "equities"
        url = f"https://www.nseindia.com/api/option-chain-{kind}?symbol={symbol}"
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9",
             "Referer": "https://www.nseindia.com/option-chain"}
        s = requests.Session()
        s.get("https://www.nseindia.com/option-chain", headers=h, timeout=10)  # cookie warm-up
        r = s.get(url, headers=h, timeout=10)
        r.raise_for_status()
        data = r.json()["records"]["data"]
        out: dict = {}
        for row in data:
            exp, k = row.get("expiryDate"), row.get("strikePrice")
            if not exp or k is None:
                continue
            slot = out.setdefault(exp, {}).setdefault(float(k), {})
            for leg, name in (("CE", "ce"), ("PE", "pe")):
                if row.get(leg) and row[leg].get("lastPrice") is not None:
                    slot[name] = {"ltp": float(row[leg]["lastPrice"]),
                                  "iv": row[leg].get("impliedVolatility"),
                                  "oi": row[leg].get("openInterest"),
                                  "bid": row[leg].get("bidprice") or row[leg].get("bidPrice"),
                                  "ask": row[leg].get("askPrice") or row[leg].get("askprice")}
        return out or None
    except Exception:
        return None

CHAIN_HISTORY_FILE = TRADING_BOT_DIR / "option_chain_history.jsonl"

def _log_chain_snapshot(symbol: str, chain: dict):
    """[V10.1] Append a compact chain snapshot (nearest expiry, ATM±4 strikes) to the
    IV/premium history dataset. Free historical option data does not exist for NSE —
    this file, built daily, becomes the options backtest/training dataset over time."""
    try:
        exp = next(iter(chain))
        strikes = sorted(chain[exp].keys())
        mid = strikes[len(strikes) // 2]
        i = strikes.index(mid)
        band = strikes[max(0, i - 4): i + 5]
        snap = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "symbol": symbol, "expiry": exp,
                "strikes": {str(k): chain[exp][k] for k in band}}
        with open(CHAIN_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, default=str) + "\n")
    except Exception:
        pass                                             # history capture never blocks trading

def fetch_option_chain(symbol: str) -> dict | None:
    """Real option-chain premiums for defined-risk leg building, as {expiry: {strike: {ce, pe}}}.
    Tries Angel One FIRST (instrument master + getMarketData — reliable, uses the existing
    account), then the unofficial NSE endpoint as fallback. None on total failure (caller then
    takes EQUITY). 10-min cache; premiums are never fabricated. Successful fetches are
    snapshotted to option_chain_history.jsonl (the future options training dataset)."""
    key = f"oc:{symbol}"
    hit = _NSE_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    out = None
    try:
        import live_quotes as lq
        out = lq.build_option_chain(symbol)
    except Exception:
        out = None
    if out is None:
        out = _chain_from_nse(symbol)
    if out:
        _log_chain_snapshot(symbol, out)
    _NSE_CACHE[key] = (time.time(), out)
    return out

def _pick_expiry(chain: dict) -> str | None:
    """Nearest expiry with >= OPT_EXIT_DTE+10 calendar days left (avoid expiry-week
    entries — stock options carry physical delivery risk; playbook rule)."""
    best, best_d = None, None
    for exp in chain:
        try:
            d = datetime.strptime(exp, "%d-%b-%Y").date()
        except ValueError:
            continue
        days = (d - date.today()).days
        if days >= OPT_EXIT_DTE + 10 and (best_d is None or days < best_d):
            best, best_d = exp, days
    return best

def _nearest_strike(strikes: list[float], px: float) -> float:
    return min(strikes, key=lambda k: abs(k - px))

def build_option_legs(instrument: str, price: float, target: float, stop: float,
                      chain: dict, strict: bool = False) -> dict | None:
    """Construct defined-risk legs from REAL chain premiums. Returns
    {legs, expiry, net_debit (per share, negative=credit), max_loss_per_share}
    or None when the chain can't support the structure (caller falls back).
    strict=True (single-stock options): each leg must also have OI > 0 and, when
    bid/ask is present, a spread <= MAX_LEG_SPREAD_PCT of mid — otherwise the LTP
    is untradeable fiction and we refuse the structure."""
    expiry = _pick_expiry(chain)
    if not expiry:
        return None
    book = chain[expiry]
    strikes = sorted(book.keys())
    if len(strikes) < 3:
        return None
    atm = _nearest_strike(strikes, price)
    i = strikes.index(atm)

    def prem(strike, leg):
        node = book.get(strike, {}).get(leg)
        if not node or not node.get("ltp") or node["ltp"] <= 0:
            return None
        if strict:
            if not node.get("oi"):
                return None                          # zero open interest = no market
            bid, ask = node.get("bid"), node.get("ask")
            if bid and ask and float(ask) > float(bid) > 0:
                mid = (float(bid) + float(ask)) / 2
                if (float(ask) - float(bid)) / mid * 100 > MAX_LEG_SPREAD_PCT:
                    return None                      # spread wider than 5% of mid
        return node["ltp"]

    if instrument == "ATM_CALL":
        p = prem(atm, "ce")
        if p is None: return None
        legs = [{"type": "CE", "side": "BUY", "strike": atm, "entry_premium": p}]
        debit = p
        max_loss = debit                                      # premium paid
    elif instrument == "BULL_CALL_SPREAD":
        upper = _nearest_strike(strikes, target)
        if upper <= atm and i + 1 < len(strikes):
            upper = strikes[i + 1]
        b, sl_ = prem(atm, "ce"), prem(upper, "ce")
        if b is None or sl_ is None or upper <= atm: return None
        legs = [{"type": "CE", "side": "BUY",  "strike": atm,   "entry_premium": b},
                {"type": "CE", "side": "SELL", "strike": upper, "entry_premium": sl_}]
        debit = b - sl_
        if debit <= 0: return None
        max_loss = debit                                      # net debit
    elif instrument == "BULL_PUT_SPREAD":
        lower = _nearest_strike(strikes, stop)
        if lower >= atm and i - 1 >= 0:
            lower = strikes[i - 1]
        s_, b_ = prem(atm, "pe"), prem(lower, "pe")
        if s_ is None or b_ is None or lower >= atm: return None
        credit = s_ - b_
        if credit <= 0: return None
        legs = [{"type": "PE", "side": "SELL", "strike": atm,   "entry_premium": s_},
                {"type": "PE", "side": "BUY",  "strike": lower, "entry_premium": b_}]
        debit = -credit
        max_loss = (atm - lower) - credit                     # width - credit
    else:
        return None
    if max_loss <= 0:
        return None
    return {"legs": legs, "expiry": expiry, "net_debit": round(debit, 2),
            "max_loss_per_share": round(max_loss, 2)}

# ───────────────────────────── valuation ─────────────────────────────
def _option_mark(pos: dict) -> tuple[float, str, list[float]]:
    """Mark an options position per share: chain LTP if available, else intrinsic
    value (flagged — intrinsic ignores time value, so the mark is conservative
    for long premium and optimistic for short). Returns (net, quality, per-leg marks)."""
    chain = fetch_option_chain(pos["symbol"])
    bar = _bar(pos)
    spot = bar["close"] if bar else pos["underlying_entry"]
    total, quality, leg_marks = 0.0, "chain", []
    book = (chain or {}).get(pos["expiry"], {})
    for leg in pos["legs"]:
        node = book.get(float(leg["strike"]), {}).get(leg["type"].lower())
        if node and node.get("ltp") is not None:
            v = node["ltp"]
        else:
            quality = "intrinsic_fallback"
            v = max(spot - leg["strike"], 0.0) if leg["type"] == "CE" else max(leg["strike"] - spot, 0.0)
        leg_marks.append(v)
        total += v if leg["side"] == "BUY" else -v
    return round(total, 2), quality, leg_marks

def position_value(pos: dict) -> float:
    """Current market value of a position in Rs (what closing it returns, ex-costs)."""
    if pos["instrument"] == "EQUITY":
        bar = _last_bar(pos["symbol"])
        px = bar["close"] if bar else pos["entry_price"]
        return px * pos["qty"]
    mark, _, _ = _option_mark(pos)
    return mark * pos["lots"] * pos["lot_size"]

def portfolio_equity(state: dict) -> float:
    return round(state["cash"] + sum(position_value(p) for p in state["positions"]), 2)

# ───────────────────────────── entries ─────────────────────────────
def _heat(state: dict) -> float:
    return sum(p.get("open_risk_inr", 0.0) for p in state["positions"])

def _entry_blockers(state: dict, reg: dict) -> list[str]:
    out = []
    if state["halted"]:
        out.append(f"kill switch active: {state['halt_reason']}")
    if not reg.get("f1"):
        out.append("F1 veto — Nifty below 50dma, no new longs")
    if reg.get("vix_tier") == "EXTREME":
        out.append(f"VIX {reg.get('vix')} EXTREME — entries halted")
    if len(state["positions"]) >= MAX_POSITIONS:
        out.append(f"max positions ({MAX_POSITIONS}) reached")
    return out

def _features(tech: dict, fund: dict, rec: dict) -> dict:
    """Feature snapshot stored on every decision — the future ML dataset."""
    reg = tech.get("market_regime", {})
    ml = tech.get("ml") or {}
    return {"score": tech.get("score"), "rs_rank": tech.get("rs_rank"),
            "n_confirmations": tech.get("n_confirmations"), "adx": tech.get("adx"),
            "rsi": tech.get("rsi"), "rsi2": tech.get("rsi2"),
            "atr_pct": tech.get("atr_pct"), "vol_ratio": tech.get("vol_ratio"),
            "mom_12_1_pct": tech.get("mom_12_1_pct"),
            "pct_from_52w_high": tech.get("pct_from_52w_high"),
            "obv_rising": tech.get("obv_rising"), "breakout_volume": tech.get("breakout_volume"),
            "ml_prob": ml.get("prob"), "quality_score": fund.get("quality_score"),
            "news_sentiment": fund.get("news_sentiment"),
            "earnings_in_days": fund.get("earnings_in_days"),
            "regime_label": reg.get("label"), "vix": reg.get("vix"),
            "vix_pctile": reg.get("vix_pctile"), "vix_slope_5d": reg.get("vix_slope_5d"),
            "conviction": rec.get("conviction"), "instrument_proposed": rec.get("instrument"),
            "engine": rec.get("engine")}

def execute_entry(bundle: dict, state: dict) -> dict:
    """Try to enter one recommendation. Returns a decision record."""
    tech, fund, rec = bundle["technical"], bundle["fundamental"], bundle["recommendation"]
    sym = tech["symbol"]
    equity = portfolio_equity(state)
    feats = _features(tech, fund, rec)
    dec = {"symbol": sym, "rec_action": rec.get("action"), "features": feats,
           "action_taken": "SKIP", "skip_reason": None}

    if rec.get("action") != "BUY":
        dec["skip_reason"] = f"strategist said {rec.get('action')}"
        return dec
    if (rec.get("conviction") or 0) < CONVICTION_MIN:
        dec["skip_reason"] = f"conviction {rec.get('conviction')} < {CONVICTION_MIN}"
        return dec
    if any(p["symbol"] == sym for p in state["positions"]):
        dec["skip_reason"] = "already holding this symbol"
        return dec

    price, stop, target = tech["price"], tech["stop_loss"], tech["target"]
    atr = tech["atr"]
    if not stop or not target or price <= stop:
        dec["skip_reason"] = "no valid stop/target from Agent 1"
        return dec

    # ----- live price overlay (Angel One; yahoo fallback handled inside) -----
    # Signals are computed on daily data; FILLS happen at the live price. If the
    # stock has already run away from the entry zone, we refuse to chase.
    data_source = "daily_delayed"
    live = None
    if lq:
        try:
            live = lq.get_quote(sym)
        except Exception:
            live = None
    if live and live.get("date") == str(date.today()):
        px_now = live["ltp"]
        data_source = f"live_{live['source']}"
        ez = rec.get("entry_zone") or tech.get("entry_zone")
        if ez and px_now > ez[1] * (1 + ENTRY_ZONE_TOL):
            dec["skip_reason"] = f"live {px_now} above entry zone {ez} — chasing forbidden"
            return dec
        if px_now <= stop:
            dec["skip_reason"] = f"live {px_now} already at/below stop {stop}"
            return dec
        # anchor risk geometry to the actual fill, same 2.5/3*ATR shape as backtest [V10.1]
        price = px_now
        stop = round(px_now - 2.5 * atr, 2)
        target = round(px_now + 3 * atr, 2)

    # ----- risk budget: min(our 1% rule, Agent 1's vol-adjusted %, Rs cap) -----
    a1_pct = (tech.get("sizing") or {}).get("risk_pct_of_capital") or RISK_PCT
    risk_budget = round(min(RISK_PCT, a1_pct) / 100 * equity, 2)
    risk_budget = min(risk_budget, RISK_RUPEE_CAP)
    if _heat(state) + risk_budget > MAX_HEAT_PCT / 100 * equity:
        dec["skip_reason"] = (f"portfolio heat {_heat(state):.0f} + {risk_budget:.0f} "
                              f"would exceed {MAX_HEAT_PCT}% of equity")
        return dec

    # ----- instrument: options only with real chain data + real lot size -----
    instrument, opt, fallback_note = "EQUITY", None, None
    proposed = rec.get("instrument")
    if proposed in ("ATM_CALL", "BULL_CALL_SPREAD", "BULL_PUT_SPREAD"):
        if (rec.get("conviction") or 0) < OPT_CONVICTION_MIN:
            fallback_note = f"options need conviction >= {OPT_CONVICTION_MIN} — taking EQUITY"
        elif sum(1 for p in state["positions"] if p["instrument"] != "EQUITY") >= MAX_OPTION_POS:
            fallback_note = f"max {MAX_OPTION_POS} option positions — taking EQUITY"
        else:
            lot = _lot_size(sym)
            if lot <= 1:
                fallback_note = "no F&O lot size in watchlist — taking EQUITY"
            else:
                chain = fetch_option_chain(sym)
                if not chain:
                    fallback_note = "NSE option chain unavailable — taking EQUITY"
                else:
                    built = build_option_legs(proposed, price, target, stop, chain, strict=True)
                    if not built:
                        fallback_note = ("chain premiums illiquid or unusable "
                                         "(OI/spread/zero-debit checks) — taking EQUITY")
                    else:
                        max_loss_lot = built["max_loss_per_share"] * lot
                        lots = math.floor(risk_budget / max_loss_lot)
                        if lots < 1:
                            fallback_note = (f"1 lot max-loss Rs{max_loss_lot:,.0f} > "
                                             f"risk budget Rs{risk_budget:,.0f} — taking EQUITY")
                        else:
                            instrument, opt = proposed, {**built, "lots": lots, "lot_size": lot}

    now = datetime.now()
    pid = state["next_id"]; state["next_id"] += 1

    if instrument == "EQUITY":
        fill = round(price * (1 + SLIPPAGE_EQ_PCT / 100), 2)
        qty = math.floor(risk_budget / (fill - stop))
        max_val = MAX_POS_VALUE_PCT / 100 * equity
        if qty * fill > max_val:
            qty = math.floor(max_val / fill)
        if qty < 1:
            dec["skip_reason"] = "sized to 0 shares (risk budget vs stop distance)"
            return dec
        cost = transaction_cost("EQUITY", "BUY", fill, qty)
        outlay = fill * qty + cost
        if outlay > state["cash"]:
            dec["skip_reason"] = f"insufficient cash Rs{state['cash']:,.0f} for Rs{outlay:,.0f}"
            return dec
        state["cash"] = round(state["cash"] - outlay, 2)
        pos = {"id": pid, "symbol": sym, "instrument": "EQUITY", "direction": "LONG",
               "entry_date": str(date.today()), "entry_time": now.strftime("%H:%M"),
               "signal_price": price, "entry_price": fill, "qty": qty, "qty_initial": qty,
               "initial_stop": stop, "stop": stop, "target_half": target,
               "half_banked": False, "reached_1atr": False,
               "atr_at_entry": atr, "highest_close": price,
               "sessions_held": 0, "last_session": None,
               "open_risk_inr": round((fill - stop) * qty, 2),
               "entry_costs": cost, "status": "OPEN", "data_source": data_source,
               "reasoning": rec.get("summary"), "features": feats}
    else:
        lots, lot = opt["lots"], opt["lot_size"]
        base = lots * lot
        slip = SLIPPAGE_OPT_STOCK                       # stock options: 2% honest slippage
        debit = opt["net_debit"] * (1 + slip / 100) if opt["net_debit"] > 0 \
                else opt["net_debit"] * (1 - slip / 100)
        cost = sum(transaction_cost("OPTIONS", leg["side"], leg["entry_premium"], lots, lot)
                   for leg in opt["legs"])
        cash_out = debit * base + cost          # credit structures: debit<0 adds cash
        max_loss = opt["max_loss_per_share"] * base
        if max(cash_out, 0) > state["cash"]:
            dec["skip_reason"] = "insufficient cash for option premium"
            return dec
        state["cash"] = round(state["cash"] - cash_out, 2)
        pos = {"id": pid, "symbol": sym, "instrument": instrument, "direction": "LONG",
               "entry_date": str(date.today()), "entry_time": now.strftime("%H:%M"),
               "underlying_entry": price, "legs": opt["legs"], "expiry": opt["expiry"],
               "lots": lots, "lot_size": lot, "net_debit": round(debit, 2),
               "stop": stop, "target": target, "reached_1atr": False,
               "atr_at_entry": atr, "sessions_held": 0, "last_session": None,
               "max_loss_inr": round(max_loss, 2),
               "open_risk_inr": round(max_loss, 2),
               "entry_costs": round(cost, 2), "status": "OPEN",
               "slippage_pct": slip, "data_source": data_source,
               "reasoning": rec.get("summary"), "features": feats}

    state["positions"].append(pos)
    dec.update({"action_taken": "ENTER", "instrument": instrument,
                "position_id": pid, "fallback_note": fallback_note,
                "risk_budget_inr": risk_budget})
    return dec

# ───────────────────────────── index sleeve (NIFTY / BANKNIFTY) ─────────────────────────────
def _index_signal(idx: dict) -> dict | None:
    """7-point long-only trend score on the index's own daily series.
    (RS-rank / news / quality don't apply to an index — this replaces Agent 1-2 for indices.)"""
    df = _index_history(idx["yahoo"])
    if df is None or len(df) < 120:
        return None
    c, h = df.Close, df.High
    price = float(c.iloc[-1])
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    atr = float(agents._atr(df).iloc[-1])
    rsi = float(agents._rsi(c).iloc[-1])
    adx = float(agents._adx(df).iloc[-1]) if len(df) >= 30 else None
    det = {"stack_20_50":  bool(price > float(ma20.iloc[-1]) > float(ma50.iloc[-1])),   # 2 pts
           "ma50_rising":  bool(float(ma50.iloc[-1]) > float(ma50.iloc[-21])),          # 1
           "breakout_5d":  bool((h >= h.rolling(20).max()).iloc[-5:].any()),            # 1
           "mom_63_pos":   bool(price > float(c.iloc[-63])),                            # 1
           "rsi_zone":     bool(45 <= rsi <= 70),                                       # 1
           "adx_trend":    bool(adx is not None and adx >= 18)}                         # 1
    score = (2 if det["stack_20_50"] else 0) + sum(1 for k in
             ("ma50_rising", "breakout_5d", "mom_63_pos", "rsi_zone", "adx_trend") if det[k])
    return {"symbol": idx["symbol"], "price": round(price, 2), "score": score, "max_score": 7,
            "atr": round(atr, 2), "rsi": round(rsi, 1),
            "adx": round(adx, 1) if adx is not None else None, "details": det,
            "direction": "LONG" if score >= INDEX_MIN_SCORE else "NONE",
            "stop": round(price - 2 * atr, 2), "target": round(price + 3 * atr, 2)}

def _enter_index(state: dict, idx: dict, sig: dict, reg: dict) -> dict:
    """Defined-risk index spread, Cohen vol-matrix instrument. No equity fallback
    exists for an index — if the spread can't be built, the trade is skipped."""
    sym = idx["symbol"]
    dec = {"symbol": sym, "sleeve": "index", "action_taken": "SKIP", "skip_reason": None,
           "features": {"index_score": sig["score"], "details": sig["details"],
                        "rsi": sig["rsi"], "adx": sig["adx"], "vix": reg.get("vix"),
                        "vix_pctile": reg.get("vix_pctile"), "vix_slope_5d": reg.get("vix_slope_5d"),
                        "regime_label": reg.get("label"), "conviction": INDEX_CONVICTION}}
    if sum(1 for p in state["positions"] if p["instrument"] != "EQUITY") >= MAX_OPTION_POS:
        dec["skip_reason"] = f"max {MAX_OPTION_POS} option positions"
        return dec
    vp, slope = reg.get("vix_pctile"), reg.get("vix_slope_5d") or 0
    if vp is not None and vp > 70:
        if slope > 0:
            dec["skip_reason"] = "VIX pctile>70 and rising — short premium forbidden, no fallback"
            return dec
        instrument = "BULL_PUT_SPREAD"
    else:
        instrument = "BULL_CALL_SPREAD"
    # live overlay: strikes and risk geometry anchored to the CURRENT index level
    px, stop, target, data_source = sig["price"], sig["stop"], sig["target"], "daily_delayed"
    if lq:
        try:
            q = lq.get_quote(sym)
            if q and q.get("date") == str(date.today()):
                px = q["ltp"]
                stop = round(px - 2 * sig["atr"], 2)
                target = round(px + 3 * sig["atr"], 2)
                data_source = f"live_{q['source']}"
        except Exception:
            pass
    chain = fetch_option_chain(sym)
    if not chain:
        dec["skip_reason"] = "NSE option chain unavailable"
        return dec
    built = build_option_legs(instrument, px, target, stop, chain)
    if not built:
        dec["skip_reason"] = f"chain premiums could not build {instrument}"
        return dec
    equity = portfolio_equity(state)
    risk_budget = min(RISK_PCT / 100 * equity, RISK_RUPEE_CAP)
    if _heat(state) + risk_budget > MAX_HEAT_PCT / 100 * equity:
        dec["skip_reason"] = f"portfolio heat would exceed {MAX_HEAT_PCT}%"
        return dec
    lot = idx["lot_size"]
    max_loss_lot = built["max_loss_per_share"] * lot
    lots = math.floor(risk_budget / max_loss_lot)
    if lots < 1:
        dec["skip_reason"] = f"1 lot max-loss Rs{max_loss_lot:,.0f} > risk budget Rs{risk_budget:,.0f}"
        return dec
    base = lots * lot
    debit = built["net_debit"] * (1 + SLIPPAGE_OPT_PCT / 100) if built["net_debit"] > 0 \
            else built["net_debit"] * (1 - SLIPPAGE_OPT_PCT / 100)
    cost = sum(transaction_cost("OPTIONS", leg["side"], leg["entry_premium"], lots, lot)
               for leg in built["legs"])
    cash_out = debit * base + cost
    if max(cash_out, 0) > state["cash"]:
        dec["skip_reason"] = "insufficient cash for option premium"
        return dec
    state["cash"] = round(state["cash"] - cash_out, 2)
    pid = state["next_id"]; state["next_id"] += 1
    state["positions"].append(
        {"id": pid, "symbol": sym, "yahoo": idx["yahoo"], "instrument": instrument,
         "direction": "LONG", "entry_date": str(date.today()),
         "entry_time": datetime.now().strftime("%H:%M"),
         "underlying_entry": px, "legs": built["legs"], "expiry": built["expiry"],
         "lots": lots, "lot_size": lot, "net_debit": round(debit, 2),
         "slippage_pct": SLIPPAGE_OPT_PCT, "data_source": data_source,
         "stop": stop, "target": target, "reached_1atr": False,
         "atr_at_entry": sig["atr"], "sessions_held": 0, "last_session": None,
         "max_loss_inr": round(max_loss_lot * lots, 2),
         "open_risk_inr": round(max_loss_lot * lots, 2),
         "entry_costs": round(cost, 2), "status": "OPEN",
         "reasoning": (f"{sym} index trend {sig['score']}/7 "
                       f"({', '.join(k for k, v in sig['details'].items() if v)}); "
                       f"{instrument} into {built['expiry']}"),
         "features": dec["features"]})
    dec.update({"action_taken": "ENTER", "instrument": instrument, "position_id": pid,
                "lots": lots, "risk_budget_inr": risk_budget})
    return dec

def run_entry_cycle(symbols: list[str] | None = None) -> dict:
    """The morning routine: regime gates -> scan -> top candidates ->
    full 3-agent recommendation -> execute. Everything is logged."""
    with _LOCK:
        state = _load()
        _roll_day(state)
        reg = agents.market_regime()
        blockers = _entry_blockers(state, reg)
        decisions = []
        if blockers:
            _log_decision({"cycle": "entry", "blocked": blockers, "regime": reg})
            _save(state)
            _heartbeat("entry", entered=0, blocked=blockers,
                       halted=state["halted"], halt_reason=state["halt_reason"])
            return {"entered": 0, "blocked": blockers, "decisions": [], "regime": reg}

        # explicit symbols (API/test) keep watchlist-relative RS; otherwise scan the
        # configured index universe (Nifty 50/Bank/Midcap150/500) with the cheap funnel:
        # one bulk history prefetch + universe-relative RS + parallel scoring. Claude is
        # still only called on the top TOP_N_RECS survivors, so cost stays bounded.
        if symbols:
            wl, rs_map, curated_syms = symbols, None, set(symbols)
        else:
            entries = uni.load_universe(ENTRY_UNIVERSE_SCOPE)
            wl = [e["symbol"] for e in entries]
            curated_syms = {e["symbol"] for e in entries if e.get("curated")}
            agents.prefetch_history(wl)                      # one batched fetch (scale lever)
            rs_map = agents.rs_ranks(wl)                     # RS relative to the universe
        # same-day re-entry guard: a symbol exited today cannot be re-bought today
        # (prevents stop-out -> re-trigger churn in the 30-min intraday cycle)
        closed_today = {c["symbol"] for c in state["closed"]
                        if c.get("exit_date") == str(date.today())}
        held = {p["symbol"] for p in state["positions"]} | closed_today
        if rs_map is None:
            agents.rs_ranks()                               # warm cache (watchlist path)
        def _scan(sym):
            if sym in held:
                return None
            try:
                t = agents.technical_agent(sym, rs_map=rs_map)
            except Exception:
                return None
            if "error" in t or t.get("direction") != "LONG" or not t.get("liquidity_ok"):
                return None
            # [V10.1 2026-07-06] TIERED CONFIRMATION GATE: non-curated universe names need
            # ALL 3 Kestner confirmations. Backtest_lab: broad universe at conf2 = PF 1.02
            # (OOS 0.90, negative exp); conf3 lifts it to OOS PF 1.56 with IS==OOS stability.
            # Curated watchlist names keep conf>=2 (their validated config, OOS PF 2.02).
            if sym not in curated_syms and t.get("n_confirmations", 0) < 3:
                return None
            return t
        with ThreadPoolExecutor(max_workers=16) as ex:
            cands = [t for t in ex.map(_scan, wl) if t]
        cands.sort(key=lambda t: t["score"], reverse=True)

        entered = 0
        for t in cands[:TOP_N_RECS]:
            if len(state["positions"]) >= MAX_POSITIONS:
                break
            try:
                bundle = agents.recommend(t["symbol"])
            except Exception as e:
                decisions.append({"symbol": t["symbol"], "action_taken": "SKIP",
                                  "skip_reason": f"recommend() failed: {str(e)[:80]}"})
                continue
            if "error" in bundle:
                continue
            dec = execute_entry(bundle, state)
            nc = bundle.get("news_context") or {}      # Agent 6 pre-market brain (audit + ML feature)
            if nc:
                dec["news"] = {"market_bias": nc.get("market_bias"), "sector": nc.get("sector"),
                               "sector_bias": nc.get("sector_bias"),
                               "sector_confidence": nc.get("sector_confidence"),
                               "stock_flag": (nc.get("stock_flag") or {}).get("bias")}
            decisions.append(dec)
            _log_decision({**dec, "cycle": "entry"})
            if dec["action_taken"] == "ENTER":
                entered += 1

        # ----- index sleeve: NIFTY / BANKNIFTY defined-risk spreads -----
        for idx in INDEX_UNIVERSE:
            if len(state["positions"]) >= MAX_POSITIONS:
                break
            if any(p["symbol"] == idx["symbol"] for p in state["positions"]) \
                    or idx["symbol"] in closed_today:
                continue
            try:
                sig = _index_signal(idx)
            except Exception as e:
                decisions.append({"symbol": idx["symbol"], "sleeve": "index",
                                  "action_taken": "SKIP",
                                  "skip_reason": f"signal failed: {str(e)[:80]}"})
                continue
            if not sig:
                continue
            if sig["direction"] != "LONG":
                dec = {"symbol": idx["symbol"], "sleeve": "index", "action_taken": "SKIP",
                       "skip_reason": f"index trend score {sig['score']}/{sig['max_score']} "
                                      f"< {INDEX_MIN_SCORE}"}
            else:
                dec = _enter_index(state, idx, sig, reg)
            decisions.append(dec)
            _log_decision({**dec, "cycle": "entry"})
            if dec["action_taken"] == "ENTER":
                entered += 1

        _save(state)
        _heartbeat("entry", entered=entered, blocked=[],
                   halted=state["halted"], halt_reason=state["halt_reason"])
        return {"entered": entered, "blocked": [], "decisions": decisions,
                "candidates_scanned": len(cands), "regime": reg}

# ───────────────────────────── exits / monitoring ─────────────────────────────
def _close_equity(state: dict, pos: dict, px: float, frac: float, reason: str):
    qty = pos["qty"] if frac >= 1 else math.floor(pos["qty"] * frac)
    if qty < 1:
        return None
    fill = round(px * (1 - SLIPPAGE_EQ_PCT / 100), 2)
    cost = transaction_cost("EQUITY", "SELL", fill, qty)
    proceeds = fill * qty - cost
    state["cash"] = round(state["cash"] + proceeds, 2)
    # P&L net of the sell cost + this slice's share of the entry cost
    entry_cost_share = pos["entry_costs"] * qty / max(pos.get("qty_initial", pos["qty"]), 1)
    pnl = round((fill - pos["entry_price"]) * qty - cost - entry_cost_share, 2)
    state["day"]["realized_pnl"] = round(state["day"]["realized_pnl"] + pnl, 2)
    rec = {"id": pos["id"], "symbol": pos["symbol"], "instrument": "EQUITY",
           "entry_date": pos["entry_date"], "entry_price": pos["entry_price"],
           "exit_date": str(date.today()), "exit_price": fill, "qty": qty,
           "pnl": pnl, "exit_reason": reason,
           "result": "WIN" if pnl > 0 else "LOSS",
           "sessions_held": pos["sessions_held"],
           "reasoning": pos.get("reasoning"), "features": pos.get("features")}
    if frac >= 1:
        pos["status"] = "CLOSED"
    else:
        pos["qty"] -= qty
        pos["half_banked"] = True
        pos["stop"] = pos["entry_price"]                 # breakeven on remainder
        pos["open_risk_inr"] = 0.0                       # remainder risk-free at BE
        rec["note"] = "banked half; remainder trails (chandelier)"
    state["closed"].append(rec)
    return rec

def _close_options(state: dict, pos: dict, reason: str):
    mark, quality, leg_marks = _option_mark(pos)
    base = pos["lots"] * pos["lot_size"]
    slip = pos.get("slippage_pct", SLIPPAGE_OPT_PCT)     # 2% stock / 1% index
    fill = mark * (1 - slip / 100) if mark > 0 else mark * (1 + slip / 100)
    # exit cost per leg at its actual closing premium (BUY legs are sold, SELL legs bought back)
    cost = sum(transaction_cost("OPTIONS", "SELL" if l["side"] == "BUY" else "BUY",
                                leg_marks[i], pos["lots"], pos["lot_size"])
               for i, l in enumerate(pos["legs"]))
    proceeds = fill * base - cost
    state["cash"] = round(state["cash"] + proceeds, 2)
    pnl = round((fill - pos["net_debit"]) * base - cost - pos["entry_costs"], 2)
    state["day"]["realized_pnl"] = round(state["day"]["realized_pnl"] + pnl, 2)
    pos["status"] = "CLOSED"
    rec = {"id": pos["id"], "symbol": pos["symbol"], "instrument": pos["instrument"],
           "entry_date": pos["entry_date"], "net_debit": pos["net_debit"],
           "exit_date": str(date.today()), "exit_mark": round(fill, 2),
           "lots": pos["lots"], "pnl": pnl, "exit_reason": reason,
           "mark_quality": quality,
           "result": "WIN" if pnl > 0 else "LOSS",
           "sessions_held": pos["sessions_held"],
           "reasoning": pos.get("reasoning"), "features": pos.get("features")}
    state["closed"].append(rec)
    return rec

def run_monitor() -> dict:
    """The daily babysitter. Priority per position:
    stop -> half-bank at target (equity) -> chandelier trail -> time stop ->
    options expiry exit. Then kill switches + equity curve."""
    with _LOCK:
        state = _load()
        _roll_day(state)
        actions = []
        for pos in list(state["positions"]):
            bar = _bar(pos)
            if not bar:
                actions.append({"symbol": pos["symbol"], "action": "NO_DATA"})
                continue
            if pos["last_session"] != bar["date"]:
                pos["sessions_held"] += 1
                pos["last_session"] = bar["date"]
            px = bar["close"]

            if pos["instrument"] == "EQUITY":
                pos["highest_close"] = max(pos.get("highest_close", px), px)
                if not pos["reached_1atr"] and bar["high"] >= pos["entry_price"] + pos["atr_at_entry"]:
                    pos["reached_1atr"] = True
                # 1. hard stop (use intraday low — honest fill at the stop, not the close)
                if bar["low"] <= pos["stop"]:
                    r = _close_equity(state, pos, pos["stop"], 1.0,
                                      "BREAKEVEN_STOP" if pos["half_banked"] else "STOPLOSS_HIT")
                    actions.append({"symbol": pos["symbol"], "action": r["exit_reason"], "pnl": r["pnl"]})
                    continue
                # 2. bank half at +3*ATR, move stop to BE
                if not pos["half_banked"] and bar["high"] >= pos["target_half"]:
                    r = _close_equity(state, pos, pos["target_half"], 0.5, "TARGET_HALF_BANKED")
                    if r:
                        actions.append({"symbol": pos["symbol"], "action": "TARGET_HALF_BANKED", "pnl": r["pnl"]})
                # 3. chandelier trail on the remainder (only ratchets up)
                if pos["status"] == "OPEN" and pos["half_banked"]:
                    chand = round(pos["highest_close"] - 3 * pos["atr_at_entry"], 2)
                    if chand > pos["stop"]:
                        pos["stop"] = chand
                        actions.append({"symbol": pos["symbol"], "action": "TRAIL_RAISED", "stop": chand})
                # 4. day-12 time stop — only if the trade never moved +1*ATR
                if (pos["status"] == "OPEN" and not pos["reached_1atr"]
                        and pos["sessions_held"] >= TIME_STOP_SESSIONS):
                    r = _close_equity(state, pos, px, 1.0, "TIME_STOP_12D")
                    actions.append({"symbol": pos["symbol"], "action": "TIME_STOP_12D", "pnl": r["pnl"]})
            else:
                # options: managed off the UNDERLYING levels + expiry clock
                if not pos["reached_1atr"] and bar["high"] >= pos["underlying_entry"] + pos["atr_at_entry"]:
                    pos["reached_1atr"] = True
                try:
                    dte = (datetime.strptime(pos["expiry"], "%d-%b-%Y").date() - date.today()).days
                except ValueError:
                    dte = 99
                reason = None
                if bar["low"] <= pos["stop"]:
                    reason = "UNDERLYING_STOP_HIT"
                elif bar["high"] >= pos["target"]:
                    reason = "UNDERLYING_TARGET_HIT"
                elif dte <= OPT_EXIT_DTE:
                    reason = f"EXPIRY_EXIT_{dte}DTE"
                elif not pos["reached_1atr"] and pos["sessions_held"] >= TIME_STOP_SESSIONS:
                    reason = "TIME_STOP_12D"
                if reason:
                    r = _close_options(state, pos, reason)
                    actions.append({"symbol": pos["symbol"], "action": reason,
                                    "pnl": r["pnl"], "mark_quality": r["mark_quality"]})

        state["positions"] = [p for p in state["positions"] if p["status"] == "OPEN"]

        # ----- kill switches -----
        eq = portfolio_equity(state)
        state["peak_equity"] = max(state.get("peak_equity", eq), eq)
        if not state["halted"]:
            if state["day"]["realized_pnl"] <= -DAILY_LOSS_HALT_PCT / 100 * eq:
                state["halted"], state["halt_reason"] = True, "DAILY_LOSS"
                msg = (f"KILL SWITCH: day loss Rs{state['day']['realized_pnl']:,.0f} > "
                       f"{DAILY_LOSS_HALT_PCT}% — no new entries until tomorrow")
                actions.append({"action": "KILL_SWITCH", "reason": msg})
                notify(msg, "ALERT")
            elif eq < state["peak_equity"] * (1 - MAX_DD_HALT_PCT / 100):
                state["halted"], state["halt_reason"] = True, "MAX_DRAWDOWN"
                msg = (f"KILL SWITCH: equity Rs{eq:,.0f} is {MAX_DD_HALT_PCT}% below peak "
                       f"Rs{state['peak_equity']:,.0f} — manual reset required")
                actions.append({"action": "KILL_SWITCH", "reason": msg})
                notify(msg, "ALERT")

        # ----- equity curve (one point per date, with NIFTY benchmark) -----
        today = str(date.today())
        nifty = None
        try:
            nifty = (agents.market_regime() or {}).get("nifty")
        except Exception:
            pass
        if not state["equity_curve"] or state["equity_curve"][-1]["date"] != today:
            state["equity_curve"].append({"date": today, "equity": eq, "nifty": nifty})
        else:
            state["equity_curve"][-1]["equity"] = eq
            if nifty:
                state["equity_curve"][-1]["nifty"] = nifty

        for a in actions:
            _log_decision({**a, "cycle": "monitor"})
        _save(state)
        _heartbeat("monitor", actions=len(actions),
                   halted=state["halted"], halt_reason=state["halt_reason"])
        return {"actions": actions, "equity": eq, "open_positions": len(state["positions"]),
                "halted": state["halted"], "halt_reason": state["halt_reason"]}

# ───────────────────────────── 30-min intraday cycle ─────────────────────────────
def run_cycle() -> dict:
    """The every-30-minutes loop during market hours: manage EXITS first
    (priority — protect the book), then look for new ENTRIES if capacity
    allows. Sends a Telegram/log digest only when something actually happened.
    Safe to schedule blindly: refuses to run outside 09:15-15:35 IST weekdays."""
    now = datetime.now()
    if now.weekday() >= 5 or not ("09:15" <= now.strftime("%H:%M") <= "15:35"):
        return {"skipped": "outside market hours"}
    mon = run_monitor()
    ent = run_entry_cycle()
    lines = []
    for a in mon.get("actions", []):
        act = a.get("action")
        if act in ("NO_DATA", "TRAIL_RAISED", "KILL_SWITCH"):
            continue                       # kill switches already notify directly
        pnl = a.get("pnl")
        lines.append(f"EXIT {a.get('symbol', '?')} {act}"
                     + (f" Rs{pnl:+,.0f}" if pnl is not None else ""))
    for d in ent.get("decisions", []):
        if d.get("action_taken") == "ENTER":
            lines.append(f"ENTER {d['symbol']} {d.get('instrument', '')}".rstrip())
    if lines:
        notify(" | ".join(lines), "TRADE")
    return {"monitor": mon, "entry": ent, "notified": lines}

# ───────────────────────────── reporting ─────────────────────────────
def _benchmark(state: dict) -> dict | None:
    """Bot return vs just buying NIFTY over the same period. If alpha is negative
    after enough trades, the honest conclusion is to index the money instead."""
    pts = [p for p in state["equity_curve"] if p.get("nifty")]
    if len(pts) < 2:
        return None
    bot = (pts[-1]["equity"] / pts[0]["equity"] - 1) * 100
    nif = (pts[-1]["nifty"] / pts[0]["nifty"] - 1) * 100
    return {"since": pts[0]["date"], "bot_return_pct": round(bot, 2),
            "nifty_return_pct": round(nif, 2), "alpha_pct": round(bot - nif, 2)}

def _read_heartbeat() -> dict:
    try:
        return json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def summary() -> dict:
    state = _load()
    eq = portfolio_equity(state)
    closed = state["closed"]
    full_exits = [c for c in closed if "banked half" not in (c.get("note") or "")]
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    gross_win = sum(c["pnl"] for c in wins)
    gross_loss = -sum(c["pnl"] for c in losses)
    curve = state["equity_curve"]
    max_dd = 0.0
    peak = state["start_capital"]
    for pt in curve:
        peak = max(peak, pt["equity"])
        max_dd = max(max_dd, (peak - pt["equity"]) / peak * 100)
    open_rows = []
    for p in state["positions"]:
        val = position_value(p)
        if p["instrument"] == "EQUITY":
            upnl = round(val - p["entry_price"] * p["qty"], 2)
        else:
            upnl = round(val - p["net_debit"] * p["lots"] * p["lot_size"], 2)
        row = {**{k: p.get(k) for k in
                  ("id", "symbol", "instrument", "entry_date", "entry_price",
                   "qty", "lots", "stop", "target_half", "target",
                   "half_banked", "sessions_held", "open_risk_inr", "reasoning")},
               "market_value": round(val, 2), "unrealized_pnl": upnl}
        # detail-panel enrichment (equity): invested, live price, today's vs total P&L
        if p["instrument"] == "EQUITY" and p.get("qty"):
            invested = round(p["entry_price"] * p["qty"], 2)
            cur_px = round(val / p["qty"], 2)
            row["invested"] = invested
            row["current_price"] = cur_px
            row["total_pnl_pct"] = round(upnl / invested * 100, 2) if invested else None
            try:                                           # today's move: vs prior close, or vs entry if bought today
                base = (p["entry_price"] if p.get("entry_date") == str(date.today())
                        else float(agents.history(p["symbol"]).Close.iloc[-2]))
                row["prev_close"] = round(base, 2)
                row["today_pnl"] = round((cur_px - base) * p["qty"], 2)
                row["today_pnl_pct"] = round((cur_px / base - 1) * 100, 2) if base else None
            except Exception:
                pass
        open_rows.append(row)
    return {"start_capital": state["start_capital"], "equity": eq,
            "cash": state["cash"], "peak_equity": state["peak_equity"],
            "return_pct": round((eq / state["start_capital"] - 1) * 100, 2),
            "halted": state["halted"], "halt_reason": state["halt_reason"],
            "day": state["day"],
            "open_positions": open_rows,
            "open_heat_inr": round(_heat(state), 2),
            "closed_trades": closed[-50:],
            "stats": {"trades_closed": len(closed), "full_exits": len(full_exits),
                      "win_rate": round(len(wins) / len(closed), 3) if closed else None,
                      "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                      "avg_win": round(gross_win / len(wins), 0) if wins else None,
                      "avg_loss": round(-gross_loss / len(losses), 0) if losses else None,
                      "expectancy_inr": round(sum(c["pnl"] for c in closed) / len(closed), 0) if closed else None,
                      "max_drawdown_pct": round(max_dd, 2)},
            "benchmark": _benchmark(state),
            "heartbeat": _read_heartbeat(),
            "equity_curve": curve,
            "limits": {"risk_pct": RISK_PCT, "max_positions": MAX_POSITIONS,
                       "max_heat_pct": MAX_HEAT_PCT, "daily_loss_halt_pct": DAILY_LOSS_HALT_PCT,
                       "max_dd_halt_pct": MAX_DD_HALT_PCT,
                       "conviction_min": CONVICTION_MIN, "opt_conviction_min": OPT_CONVICTION_MIN}}

def reset_halt() -> dict:
    with _LOCK:
        state = _load()
        was = state["halt_reason"]
        state["halted"], state["halt_reason"] = False, None
        _save(state)
        _log_decision({"cycle": "admin", "action": "HALT_RESET", "was": was})
        return {"reset": True, "was": was}

# ───────────────────────────── CLI ─────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    try:
        if cmd == "execute":
            print(json.dumps(run_entry_cycle(), indent=1))
        elif cmd == "cycle":
            print(json.dumps(run_cycle(), indent=1))
        elif cmd == "monitor":
            print(json.dumps(run_monitor(), indent=1))
        elif cmd == "reset-halt":
            print(json.dumps(reset_halt(), indent=1))
        else:
            print(json.dumps(summary(), indent=1))
    except Exception as e:
        # a crashed scheduled run must never die silently
        notify(f"agent4 {cmd} CRASHED: {type(e).__name__}: {str(e)[:200]}", "ALERT")
        raise
