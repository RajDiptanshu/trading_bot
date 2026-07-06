"""
live_quotes.py — real-time prices for fills and position monitoring.

Source 1: Angel One SmartAPI (free with the existing account; reads ANGEL_API_KEY,
          ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET from C:\\trading_bot\\.env —
          the exact pattern proven in the old pipeline).
Source 2: yfinance (delayed) — automatic fallback per symbol, clearly labelled.

API:
    get_quotes(["RELIANCE", "NIFTY", ...]) ->
        {sym: {"ltp", "open", "high", "low", "prev_close", "date", "source"}}
    source is "angel" (live) or "yahoo" (delayed). Missing symbols are absent.

Index aliases (Angel tokens are well-known but VERIFY against Angel's scrip master
if index quotes look wrong; yfinance fallback covers them regardless):
    NIFTY -> token 99926000 / ^NSEI,  BANKNIFTY -> token 99926009 / ^NSEBANK
"""
from __future__ import annotations
import os, time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=False)
except Exception:
    pass

INDEX_ALIASES = {  # symbol -> (angel_token VERIFY, yahoo)
    "NIFTY":     ("99926000", "^NSEI"),
    "BANKNIFTY": ("99926009", "^NSEBANK"),
}

_SESSION = {"obj": None, "ts": 0.0}
_LOGIN_TTL = 6 * 3600          # re-login after 6h
_QUOTE_CACHE: dict = {}        # sym -> (ts, quote)
_QUOTE_TTL = 60                # seconds — scheduled runs always refetch fresh enough

def _watchlist_tokens() -> dict:
    """symbol -> angel_token from watchlist.json (single source of truth)."""
    out = {}
    try:
        import json
        with open(TRADING_BOT_DIR / "watchlist.json", encoding="utf-8") as f:
            for s in json.load(f)["stocks"]:
                if s.get("angel_token"):
                    out[s["symbol"]] = str(s["angel_token"])
    except Exception:
        pass
    return out

def _angel():
    """Cached SmartAPI session; None when login impossible (missing creds/library/network)."""
    now = time.time()
    if _SESSION["obj"] is not None and now - _SESSION["ts"] < _LOGIN_TTL:
        return _SESSION["obj"]
    try:
        import pyotp
        from SmartApi import SmartConnect
        api_key, client = os.getenv("ANGEL_API_KEY"), os.getenv("ANGEL_CLIENT_ID")
        pwd, totp_secret = os.getenv("ANGEL_PASSWORD"), os.getenv("ANGEL_TOTP_SECRET")
        if not all((api_key, client, pwd, totp_secret)):
            return None
        obj = SmartConnect(api_key=api_key)
        data = obj.generateSession(client, pwd, pyotp.TOTP(totp_secret).now())
        if not data.get("status"):
            return None
        _SESSION.update({"obj": obj, "ts": now})
        return obj
    except Exception:
        return None

# ───────────────────────────── historical candles ─────────────────────────────
# Angel One getCandleData — real-time OHLCV (no ~15-min Yahoo delay), free with the
# existing account. Used by agents.history()/chart_data() with yfinance fallback.
# VERIFIED 2026-06-16 against angel-one/smartapi-python (SmartConnect.getCandleData;
# test/api_test.py candleParams: exchange/symboltoken/interval/fromdate/todate,
# "YYYY-MM-DD HH:MM"). Interval-constant names below and the per-request max-days /
# rate limits are the standard set but should be re-checked in the live SmartAPI docs.
_ANGEL_INTERVAL = {
    "1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR", "1d": "ONE_DAY",
}

def candles_enabled() -> bool:
    """Kill switch: set ANGEL_CANDLES=0 (or false/no) to force the yfinance path."""
    return os.getenv("ANGEL_CANDLES", "1").strip().lower() not in ("0", "false", "no", "")

def _token_for(symbol: str):
    """(exchange, token) for an index alias or watchlist stock, else None."""
    if symbol in INDEX_ALIASES:
        return ("NSE", INDEX_ALIASES[symbol][0])
    tok = _watchlist_tokens().get(symbol)
    return ("NSE", tok) if tok else None

def get_candles(symbol: str, interval: str = "1d", days: int = 400):
    """Historical OHLCV from Angel One, returned in the SAME shape yfinance produces
    (tz-aware Asia/Kolkata DatetimeIndex; columns Open/High/Low/Close/Volume) so it is
    a drop-in for history()/chart_data(). Returns None on ANY problem — disabled,
    unknown token, no session, API error, empty/short data — so callers fall back to
    yfinance. NOTE: Angel candles are split/dividend UNADJUSTED (yfinance auto_adjust
    is adjusted); a corporate action inside the window shows as a raw price step."""
    if not candles_enabled():
        return None
    ang = _ANGEL_INTERVAL.get(interval)
    if ang is None:
        return None
    exch_tok = _token_for(symbol)
    if exch_tok is None:
        return None
    exchange, token = exch_tok
    obj = _angel()
    if obj is None:
        return None
    try:
        import pandas as pd
        now = datetime.now()
        frm = now - timedelta(days=max(int(days), 1))
        params = {
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": ang,
            "fromdate": frm.strftime("%Y-%m-%d 09:15"),
            "todate": now.strftime("%Y-%m-%d %H:%M"),
        }
        resp = obj.getCandleData(params)
        rows = (resp or {}).get("data") or []
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        idx = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.drop(columns=["ts"]).set_index(idx)
        df.index.name = "Datetime"
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"]).sort_index()
        return df if len(df) else None
    except Exception:
        return None

def _angel_quotes(symbols: list[str]) -> dict:
    """Batch FULL quotes (<=50 tokens/call); per-symbol ltpData fallback inside."""
    obj = _angel()
    if obj is None:
        return {}
    tokens = _watchlist_tokens()
    tok2sym = {}
    for sym in symbols:
        if sym in INDEX_ALIASES:
            tok2sym[INDEX_ALIASES[sym][0]] = sym
        elif sym in tokens:
            tok2sym[tokens[sym]] = sym
    if not tok2sym:
        return {}
    out, today = {}, str(date.today())
    toks = list(tok2sym)
    try:
        for i in range(0, len(toks), 50):
            resp = obj.getMarketData("FULL", {"NSE": toks[i:i + 50]})
            for row in (resp.get("data", {}) or {}).get("fetched", []) or []:
                sym = tok2sym.get(str(row.get("symbolToken")))
                if not sym or row.get("ltp") in (None, 0):
                    continue
                out[sym] = {"ltp": float(row["ltp"]),
                            "open": float(row.get("open") or row["ltp"]),
                            "high": float(row.get("high") or row["ltp"]),
                            "low": float(row.get("low") or row["ltp"]),
                            "prev_close": float(row.get("close") or row["ltp"]),
                            "date": today, "source": "angel"}
    except Exception:
        pass
    # per-symbol fallback for anything the batch missed (older SmartApi versions)
    for tok, sym in tok2sym.items():
        if sym in out:
            continue
        try:
            trad = sym if sym in INDEX_ALIASES else f"{sym}-EQ"
            resp = obj.ltpData("NSE", trad, tok)
            d = resp.get("data") or {}
            if resp.get("status") and d.get("ltp"):
                out[sym] = {"ltp": float(d["ltp"]),
                            "open": float(d.get("open") or d["ltp"]),
                            "high": float(d.get("high") or d["ltp"]),
                            "low": float(d.get("low") or d["ltp"]),
                            "prev_close": float(d.get("close") or d["ltp"]),
                            "date": today, "source": "angel"}
            time.sleep(0.1)
        except Exception:
            continue
    return out

def _yahoo_quote(sym: str) -> dict | None:
    try:
        import yfinance as yf
        ticker = INDEX_ALIASES[sym][1] if sym in INDEX_ALIASES else sym + ".NS"
        df = yf.Ticker(ticker).history(period="2d", interval="1d")
        df = df.dropna(subset=["Close"])
        if not len(df):
            return None
        r = df.iloc[-1]
        return {"ltp": float(r.Close), "open": float(r.Open), "high": float(r.High),
                "low": float(r.Low),
                "prev_close": float(df.iloc[-2].Close) if len(df) > 1 else float(r.Open),
                "date": df.index[-1].strftime("%Y-%m-%d"), "source": "yahoo"}
    except Exception:
        return None

def get_quotes(symbols: list[str]) -> dict:
    now = time.time()
    out, missing = {}, []
    for s in symbols:
        hit = _QUOTE_CACHE.get(s)
        if hit and now - hit[0] < _QUOTE_TTL:
            out[s] = hit[1]
        else:
            missing.append(s)
    if missing:
        live = _angel_quotes(missing)
        for s in missing:
            q = live.get(s) or _yahoo_quote(s)
            if q:
                _QUOTE_CACHE[s] = (now, q)
                out[s] = q
    return out

def get_quote(symbol: str) -> dict | None:
    return get_quotes([symbol]).get(symbol)

# ───────────────────────────── Angel option chain + F&O lot sizes ─────────────────────────────
# Angel publishes a full instrument master (every NFO option: token, strike, expiry, lotsize).
# We build the option chain from it + live LTP/OI/bid-ask via getMarketData("FULL", {"NFO": …}),
# returned in the SAME shape agent4.fetch_option_chain expects so it's a drop-in for the flaky
# unofficial NSE scraper. Also yields real F&O lot sizes for the whole universe (not just the 55).
_MASTER_URL   = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
_MASTER_FILE  = TRADING_BOT_DIR / "universe_cache" / "angel_scrip_master.json"
_MASTER_MEM   = {"ts": 0.0, "opts": None, "lots": None}

def _load_master(max_age_h: int = 24):
    """Angel instrument master (list of dicts), disk-cached 24h. [] on total failure."""
    import json
    try:
        fresh = _MASTER_FILE.exists() and (time.time() - _MASTER_FILE.stat().st_mtime) < max_age_h * 3600
        if not fresh:
            import urllib.request, ssl
            req = urllib.request.Request(_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
                data = r.read()
            _MASTER_FILE.parent.mkdir(exist_ok=True)
            _MASTER_FILE.write_bytes(data)
        return json.loads(_MASTER_FILE.read_text(encoding="utf-8"))
    except Exception:
        try:
            return json.loads(_MASTER_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

def _index_master():
    """Parse the master once -> ({underlying: [option legs]}, {underlying: lotsize}). Cached 6h.
    Angel stores option strike in paise (×100) and expiry as DDMMMYYYY; option type is the
    CE/PE suffix of the trading symbol."""
    now = time.time()
    if _MASTER_MEM["opts"] is not None and now - _MASTER_MEM["ts"] < 6 * 3600:
        return _MASTER_MEM["opts"], _MASTER_MEM["lots"]
    opts, lots = {}, {}
    for r in _load_master():
        if r.get("exch_seg") != "NFO":
            continue
        nm = (r.get("name") or "").upper()
        it = r.get("instrumenttype", "")
        if not nm:
            continue
        if it in ("FUTSTK", "FUTIDX", "OPTSTK", "OPTIDX"):       # any F&O row carries the lot size
            try:
                ls = int(float(r.get("lotsize") or 0))
                if ls > 0:
                    lots[nm] = ls
            except Exception:
                pass
        if it in ("OPTSTK", "OPTIDX"):
            sym = r.get("symbol", "")
            ot = sym[-2:].lower() if sym[-2:] in ("CE", "PE") else None
            if not ot:
                continue
            try:
                ed = datetime.strptime((r.get("expiry") or "").title(), "%d%b%Y").date()
                strike = float(r.get("strike") or 0) / 100.0
            except Exception:
                continue
            if strike <= 0:
                continue
            opts.setdefault(nm, []).append({"token": str(r.get("token")), "expiry": ed,
                                            "expiry_str": ed.strftime("%d-%b-%Y"),
                                            "strike": strike, "ot": ot})
    _MASTER_MEM.update({"ts": now, "opts": opts, "lots": lots})
    return opts, lots

def option_lot_sizes() -> dict:
    """{UNDERLYING: lot_size} for every F&O name in Angel's master."""
    return _index_master()[1]

def build_option_chain(symbol: str, underlying_price: float | None = None,
                       min_days: int = 14, window: int = 18) -> dict | None:
    """Option chain in agent4's format {expiry_str: {strike: {ce/pe: {ltp,oi,bid,ask,iv}}}}.
    Nearest expiry with >= min_days, a +-window strike band around ATM, live via getMarketData.
    None on any failure (caller falls back to the NSE scraper / EQUITY)."""
    opts, _ = _index_master()
    rows = opts.get(symbol.upper())
    if not rows:
        return None
    obj = _angel()
    if obj is None:
        return None
    exps = sorted({r["expiry"] for r in rows})
    pick = next((e for e in exps if (e - date.today()).days >= min_days), None)
    if pick is None:
        return None
    leg_rows = [r for r in rows if r["expiry"] == pick]
    if underlying_price is None:
        q = get_quote(symbol)
        underlying_price = q["ltp"] if q else None
    strikes = sorted({r["strike"] for r in leg_rows})
    if underlying_price and len(strikes) > 2 * window:
        ai = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_price))
        band = set(strikes[max(0, ai - window): ai + window + 1])
        leg_rows = [r for r in leg_rows if r["strike"] in band]
    tok2 = {r["token"]: r for r in leg_rows}
    fetched = {}
    try:
        toks = list(tok2)
        for i in range(0, len(toks), 50):
            resp = obj.getMarketData("FULL", {"NFO": toks[i:i + 50]})
            for row in (resp.get("data", {}) or {}).get("fetched", []) or []:
                fetched[str(row.get("symbolToken"))] = row
    except Exception:
        return None
    chain: dict = {}
    for tok, meta in tok2.items():
        row = fetched.get(tok)
        if not row or not row.get("ltp"):
            continue
        depth = row.get("depth") or {}
        bid = (depth.get("buy") or [{}])[0].get("price")
        ask = (depth.get("sell") or [{}])[0].get("price")
        node = {"ltp": float(row["ltp"]), "iv": None, "oi": row.get("opnInterest"),
                "bid": bid, "ask": ask}
        chain.setdefault(meta["expiry_str"], {}).setdefault(meta["strike"], {})[meta["ot"]] = node
    return chain or None

if __name__ == "__main__":
    import json as _j
    print(_j.dumps(get_quotes(["RELIANCE", "NIFTY"]), indent=1))
