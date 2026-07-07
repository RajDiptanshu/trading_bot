"""
NSE Recommendation Engine — FastAPI backend
Run:  python app.py      (serves UI at http://127.0.0.1:8650)
Reads watchlist + .env from C:\\trading_bot (override with TRADING_BOT_DIR env var).
"""
import sys, os
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1])) / ".env", override=True)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import agents
import agent4

app = FastAPI(title="NSE Recommendation Engine")
POOL = ThreadPoolExecutor(max_workers=8)

@app.get("/api/regime")
def regime():
    return agents.market_regime()

@app.get("/api/watchlist")
def watchlist():
    wl = agents.load_watchlist()
    ranks = {}
    try:
        ranks = agents.rs_ranks()
    except Exception:
        pass
    out = []
    for s in wl:
        r = ranks.get(s["symbol"], {})
        out.append({"symbol": s["symbol"], "sector": s["sector"],
                    "tier": s.get("liquidity_tier", ""), "rs_rank": r.get("rank"),
                    "ret_12m": r.get("ret_12m")})
    return out

class AddStock(BaseModel):
    symbol: str
    sector: str = "Other"
    tier: str = "MIDCAP"

@app.post("/api/watchlist/add")
def add_stock(body: AddStock):
    sym = body.symbol.strip().upper().replace(".NS", "")
    try:
        df = agents.history(sym, "3mo")
        if len(df) < 10:
            raise ValueError("no data")
    except Exception:
        raise HTTPException(400, f"'{sym}' not found on NSE (yfinance lookup failed)")
    added = agents.save_watchlist_entry(sym, body.sector, body.tier)
    return {"added": added, "symbol": sym,
            "note": "already in watchlist" if not added else "added to watchlist.json"}

@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    t = agents.technical_agent(symbol.upper())
    if "error" in t:
        raise HTTPException(404, t["error"])
    return t

@app.get("/api/chart/{symbol}")
def chart(symbol: str, period: str = "13mo"):
    if period not in ("5m", "15m", "1h", "6mo", "13mo", "3y", "5y"):
        period = "13mo"
    try:
        return agents.chart_data(symbol.upper(), period)
    except Exception as e:
        raise HTTPException(404, str(e))

@app.get("/api/news/{symbol}")
def news(symbol: str):
    return agents.fundamental_agent(symbol.upper())

@app.get("/api/news-brief")
def news_brief():
    """Latest pre-market News Brain briefing (Agent 6): market bias, sector contagion,
    stock flags, and the source headlines behind every claim."""
    import news_brain
    return news_brain.load_today_briefing(max_age_hours=999) or {
        "status": "no briefing yet", "hint": "run: python news_brain.py build"}

@app.post("/api/news-brief/build")
def news_brief_build():
    """Fetch the last 24h of news + run the Claude synthesis now (slow: ~1-2 min)."""
    import news_brain
    return news_brain.build_briefing()

@app.post("/api/recommend/{symbol}")
def recommend(symbol: str):
    out = agents.recommend(symbol.upper())
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out

@app.get("/api/scan")
def scan():
    """Score the whole watchlist (parallel, cached) — the morning-scan table."""
    wl = [s["symbol"] for s in agents.load_watchlist()]
    agents.rs_ranks()  # warm the cache once
    results = list(POOL.map(lambda s: agents.technical_agent(s), wl))
    ok = [r for r in results if "error" not in r]
    ok.sort(key=lambda r: r["score"], reverse=True)
    return {"regime": agents.market_regime(), "rows": ok,
            "errors": [r for r in results if "error" in r]}

@app.get("/api/universe")
def universe_scopes():
    """Available screener scopes + their sizes (Nifty 50/Bank/Midcap150/500)."""
    import universe as uni
    return {s: len(uni.load_universe(s)) for s in ("core", "nifty50", "nifty210", "nifty500")}

@app.get("/api/screener")
def screener(scope: str = "nifty210", sector: str | None = None, min_score: int = 0,
             direction: str | None = None, liquid_only: bool = False, limit: int = 120):
    """Scan a full NSE index universe with the cheap technical funnel + News Brain
    sector overlay. NO Claude calls in the bulk scan — drill into any name via
    POST /api/recommend/{sym} for the full 3-agent + Claude buy/not-buy verdict."""
    import screener_engine
    if scope not in ("core", "nifty50", "nifty210", "nifty500", "all"):
        scope = "nifty210"
    return screener_engine.scan_universe(scope, min_score=min_score, sector=sector,
                                         direction=direction, liquid_only=liquid_only, limit=limit)

# ───────────── Agent 4 — paper-trading portfolio (Rs 20,00,000) ─────────────
@app.get("/api/portfolio")
def portfolio():
    """Portfolio summary: equity, positions, closed trades, stats, kill-switch state."""
    return agent4.summary()

class ExecuteBody(BaseModel):
    symbols: list[str] | None = None   # default: whole watchlist

@app.post("/api/portfolio/execute")
def portfolio_execute(body: ExecuteBody | None = None):
    """Entry cycle: regime gates -> scan -> top candidates -> 3-agent rec -> enter."""
    return agent4.run_entry_cycle(body.symbols if body else None)

@app.post("/api/portfolio/monitor")
def portfolio_monitor():
    """Manage open positions: stops, half-bank, trail, time stop, expiry, kill switches."""
    return agent4.run_monitor()

@app.post("/api/portfolio/reset-halt")
def portfolio_reset_halt():
    """Manually clear the MAX_DRAWDOWN kill switch (deliberate human decision)."""
    return agent4.reset_halt()

class ChatBody(BaseModel):
    messages: list[dict]     # [{role: user|assistant, content: str}, ...] ending with user

@app.post("/api/chat")
def chat(body: ChatBody):
    """Conversational analyst: agentic Claude loop grounded in the system's own tools
    (live quotes, technicals, news, briefing, recommendation, option chain, screener,
    portfolio). Client holds history and sends it each turn (server stays stateless)."""
    import chat_analyst
    return chat_analyst.chat(body.messages)

@app.get("/api/report")
def report(rebuild: bool = False):
    """Agent 5 — daily performance report: equity/P&L, win-rate/PF/payoff/expectancy,
    drawdown, benchmark vs NIFTY, go-live gate scorecard, today's activity, and a Claude
    EOD note. Returns the latest saved report; rebuild=true recomputes it."""
    import agent5_report
    if rebuild:
        return agent5_report.build_report()
    return agent5_report.load_latest() or agent5_report.build_report()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")

if __name__ == "__main__":
    import uvicorn
    print("\n  NSE Recommendation Engine -> http://127.0.0.1:8650\n")
    uvicorn.run(app, host="127.0.0.1", port=8650, log_level="warning")
