"""
chat_analyst.py — the cockpit's conversational stock analyst (2026-07-07).

POST /api/chat backend: an agentic Claude loop that answers free-form questions
("what's happening with RELIANCE?", "should I buy TCS?", "best IT setups right now?",
"how does a bull call spread work here?") by CALLING THE SYSTEM'S OWN DATA FUNCTIONS
as tools — live quotes, technicals, per-stock news, the pre-market briefing, the full
3-agent recommendation, option chains, the screener, and the paper portfolio.

The grounding rule that makes this trustworthy: Claude may not state a price, level,
or headline it did not receive from a tool result in THIS conversation. If a tool
fails, it says so instead of guessing. Same DNA as the rest of the system — Claude
judges, Python computes; nothing is fabricated.

Costs: each question = 1-4 Claude calls (tool loop), a few cents. History is capped
client-side; tool outputs are trimmed hard so tokens stay bounded.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=False)
except Exception:
    pass

MODEL      = os.getenv("CHAT_MODEL", os.getenv("STRATEGIST_MODEL", "claude-sonnet-4-6"))
MAX_STEPS  = 8            # tool-loop iterations per question (hard bound)
MAX_TOKENS = 1400
HISTORY_CAP = 16          # messages of history accepted from the client

def _sym(s: str) -> str:
    return (s or "").upper().replace(".NS", "").strip()

# ───────────────────────────── tool implementations ─────────────────────────────
# Each returns a COMPACT dict (trimmed hard — these go into the prompt as JSON).

def _t_price(symbol: str) -> dict:
    import live_quotes as lq
    q = lq.get_quote(_sym(symbol))
    if not q:
        return {"error": f"no quote available for {symbol}"}
    chg = round((q["ltp"] / q["prev_close"] - 1) * 100, 2) if q.get("prev_close") else None
    return {"symbol": _sym(symbol), "ltp": q["ltp"], "open": q.get("open"),
            "high": q.get("high"), "low": q.get("low"), "prev_close": q.get("prev_close"),
            "change_pct": chg, "as_of": q.get("date"),
            "source": q.get("source"), "note": "yahoo source is ~15min delayed"}

def _t_technicals(symbol: str) -> dict:
    import agents
    t = agents.technical_agent(_sym(symbol))
    if "error" in t:
        return {"error": t["error"]}
    keys = ("symbol", "price", "score", "max_score", "min_score_required", "direction",
            "f1_veto", "confirmations", "n_confirmations", "momentum_veto", "rsi", "rsi2",
            "adx", "atr", "atr_pct", "ma20", "ma50", "ma200", "high_52w", "low_52w",
            "pct_from_52w_high", "rs_rank", "ret_12m_pct", "mom_12_1_pct", "vol_ratio",
            "obv_rising", "breakout_volume", "mean_reversion_setup", "stop_loss", "target",
            "entry_zone", "liquidity_ok", "avg_traded_value_cr", "ml")
    out = {k: t.get(k) for k in keys}
    out["market_regime"] = {k: t.get("market_regime", {}).get(k) for k in
                            ("label", "f1", "vix", "vix_tier", "vix_pctile", "nifty")}
    return out

def _t_news(symbol: str) -> dict:
    import agents
    f = agents.fundamental_agent(_sym(symbol))
    return {"symbol": _sym(symbol),
            "headlines": f.get("headlines", [])[:8],
            "news_window_hours": f.get("news_window_hours"),
            "news_sentiment": f.get("news_sentiment"),
            "kill_word_hits": f.get("kill_word_hits", [])[:3],
            "earnings_in_days": f.get("earnings_in_days"),
            "earnings_risk": f.get("earnings_risk"),
            "quality": f.get("quality"), "quality_score": f.get("quality_score")}

def _t_briefing() -> dict:
    import agents
    out = {"regime": agents.market_regime()}
    try:
        import news_brain
        b = news_brain.load_today_briefing(max_age_hours=30)
        if b:
            flags = list((b.get("stock_flags") or {}).items())[:10]
            out["briefing"] = {"date": b.get("date"), "market_bias": b.get("market_bias"),
                               "market_bias_reason": b.get("market_bias_reason"),
                               "sector_view": b.get("sector_view"),
                               "stock_flags": dict(flags), "summary": b.get("summary")}
        else:
            out["briefing"] = None
            out["note"] = "no fresh pre-market briefing (runs 08:30 weekdays)"
    except Exception as e:
        out["briefing_error"] = str(e)[:80]
    return out

def _t_recommend(symbol: str) -> dict:
    import agents
    r = agents.recommend(_sym(symbol))
    if "error" in r:
        return {"error": r["error"]}
    rec = r.get("recommendation", {})
    return {"symbol": _sym(symbol), "generated": r.get("generated"),
            "action": rec.get("action"), "instrument": rec.get("instrument"),
            "conviction": rec.get("conviction"),
            "entry_zone": rec.get("entry_zone"), "stop_loss": rec.get("stop_loss"),
            "target": rec.get("target"), "exit_plan": rec.get("exit_plan"),
            "reasons_for": rec.get("reasons_for"), "reasons_against": rec.get("reasons_against"),
            "what_invalidates": rec.get("what_invalidates"), "summary": rec.get("summary"),
            "expected_value_note": rec.get("expected_value_note"),
            "news_context": r.get("news_context"), "engine": rec.get("engine")}

def _t_chain(symbol: str) -> dict:
    import agent4, live_quotes as lq
    s = _sym(symbol)
    ch = agent4.fetch_option_chain(s)
    if not ch:
        return {"error": f"option chain unavailable for {s} (not F&O, or feed down)"}
    q = lq.get_quote(s)
    px = q["ltp"] if q else None
    exp = next(iter(ch))
    strikes = sorted(ch[exp].keys())
    if px:
        ai = min(range(len(strikes)), key=lambda i: abs(strikes[i] - px))
        band = strikes[max(0, ai - 4): ai + 5]
    else:
        band = strikes[:9]
    rows = []
    for k in band:
        n = ch[exp][k]
        rows.append({"strike": k,
                     "ce": {x: (n.get("ce") or {}).get(x) for x in ("ltp", "oi", "bid", "ask")},
                     "pe": {x: (n.get("pe") or {}).get(x) for x in ("ltp", "oi", "bid", "ask")}})
    return {"symbol": s, "underlying_ltp": px, "expiry": exp,
            "lot_size": agent4._lot_size(s), "atm_band": rows,
            "note": "premiums are live LTP; defined-risk structures only per system rules"}

def _t_screen(scope: str = "nifty210", sector: str | None = None,
              min_score: int = 0, direction: str | None = "LONG", limit: int = 12) -> dict:
    import screener_engine
    if scope not in ("core", "nifty50", "nifty210", "nifty500"):
        scope = "nifty210"
    res = screener_engine.scan_universe(scope, min_score=min_score, sector=sector or None,
                                        direction=direction or None, limit=min(int(limit), 20))
    rows = [{k: r.get(k) for k in ("symbol", "sector", "score", "min_score", "direction",
                                   "rs_rank", "ret_12m", "atr_pct", "news_sector_bias",
                                   "news_stock_flag", "turnover_cr")} for r in res["rows"]]
    return {"scope": res["scope"], "universe_size": res["universe_size"],
            "cached": res["cached"], "rows": rows,
            "note": "first uncached scan takes ~30-60s; rows ranked by score then RS"}

def _t_portfolio() -> dict:
    import agent4
    s = agent4.summary()
    pos = [{k: p.get(k) for k in ("symbol", "instrument", "entry_price", "current_price",
                                  "qty", "unrealized_pnl", "sessions_held", "stop")}
           for p in s.get("open_positions", [])]
    return {"equity": s.get("equity"), "return_pct": s.get("return_pct"),
            "cash": s.get("cash"), "halted": s.get("halted"),
            "open_positions": pos, "stats": s.get("stats"),
            "benchmark_vs_nifty": s.get("benchmark"),
            "note": "PAPER portfolio (virtual Rs 20L) — no real money"}

TOOL_IMPLS = {
    "get_stock_price": lambda a: _t_price(a["symbol"]),
    "get_technicals": lambda a: _t_technicals(a["symbol"]),
    "get_stock_news": lambda a: _t_news(a["symbol"]),
    "get_market_briefing": lambda a: _t_briefing(),
    "get_full_recommendation": lambda a: _t_recommend(a["symbol"]),
    "get_option_chain": lambda a: _t_chain(a["symbol"]),
    "screen_market": lambda a: _t_screen(a.get("scope", "nifty210"), a.get("sector"),
                                         int(a.get("min_score", 0) or 0),
                                         a.get("direction", "LONG"), int(a.get("limit", 12) or 12)),
    "get_portfolio": lambda a: _t_portfolio(),
}

_SYM_PROP = {"type": "object", "properties": {"symbol": {"type": "string",
             "description": "NSE symbol, e.g. RELIANCE, TCS, NIFTY"}}, "required": ["symbol"]}

TOOLS = [
    {"name": "get_stock_price", "description":
     "Live/latest price for an NSE stock or index (LTP, OHLC, prev close, % change, data age).",
     "input_schema": _SYM_PROP},
    {"name": "get_technicals", "description":
     "Full technical workup: 14-pt score, trend direction, RSI/ADX/ATR, moving averages, 52w range, "
     "relative strength rank, trend confirmations, suggested stop/target, market regime. Use for "
     "pattern/setup questions.", "input_schema": _SYM_PROP},
    {"name": "get_stock_news", "description":
     "Recent (last 24-48h, timestamped) headlines for one stock + sentiment, kill-words, earnings "
     "proximity, fundamental quality snapshot. Use for 'why is X up/down' together with price + briefing.",
     "input_schema": _SYM_PROP},
    {"name": "get_market_briefing", "description":
     "Market-wide context: NIFTY regime (trend/VIX) + today's pre-market news briefing (market bias, "
     "sector contagion views, flagged stocks). Use for market-level questions and sector read-across.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_full_recommendation", "description":
     "The system's complete 3-agent verdict on one stock: BUY/WAIT/AVOID + conviction + entry/stop/target "
     "+ reasons for and against + what invalidates. SLOW (~5-20s). Use when asked 'should I buy/sell X'.",
     "input_schema": _SYM_PROP},
    {"name": "get_option_chain", "description":
     "Live option chain for an F&O stock or NIFTY/BANKNIFTY: nearest expiry, lot size, ATM±4 strikes with "
     "CE/PE premium, OI, bid/ask. Use for options questions and strike selection.", "input_schema": _SYM_PROP},
    {"name": "screen_market", "description":
     "Rank the NSE universe by the technical funnel. Filters: scope (core/nifty50/nifty210/nifty500), "
     "sector (e.g. IT, Banking, Pharma), min_score (0-14), direction (LONG or empty), limit. Use for "
     "'best setups right now' / 'top stocks in sector X'.",
     "input_schema": {"type": "object", "properties": {
         "scope": {"type": "string"}, "sector": {"type": "string"},
         "min_score": {"type": "integer"}, "direction": {"type": "string"},
         "limit": {"type": "integer"}}}},
    {"name": "get_portfolio", "description":
     "The user's PAPER portfolio: equity, open positions with live P&L, closed-trade stats, alpha vs NIFTY.",
     "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = f"""You are the resident analyst inside the user's own NSE trading cockpit (Indian equities + F&O).
Today is {{today}}. You have tools wired into the user's live system — quotes, technicals, news,
the pre-market briefing, the 3-agent recommendation engine, option chains, the screener, and their
paper portfolio.

GROUNDING RULES (non-negotiable):
- Every price, level, headline, premium, or statistic you state MUST come from a tool result in this
  conversation. If you didn't fetch it, don't say it. If a tool fails, say what failed — never guess.
- Always mention data freshness when relevant (live vs ~15min delayed; market closed => last close).
- Numbers in INR. NSE market hours 09:15–15:30 IST, Mon–Fri.

HOW TO WORK:
- "Why is X up/down?" => get_stock_price + get_stock_news + get_market_briefing; connect the move to
  specific headlines/sector bias; if nothing explains it, say the move has no obvious news driver.
- "Should I buy/sell X?" => get_full_recommendation (the system's validated verdict) and present its
  action, conviction, levels, and BOTH reasons-for and reasons-against. You may add your own read from
  technicals/news, clearly labelled as your read vs the system's.
- Pattern/setup questions => get_technicals; explain what the indicators mean in plain language.
- Options questions => get_option_chain; the system only ever uses defined-risk structures (ATM call,
  bull call spread, bull put spread) — explain max loss/gain with the REAL premiums you fetched.
- Concept/education questions ("what is a bull call spread?") need no tools — answer directly, and
  offer to illustrate with a live chain.
- Be concise: lead with the answer, then the evidence. Plain language, no filler.

FORMAT (the chat UI renders PLAIN TEXT — markdown will show as raw symbols):
- No markdown headers, tables, bold (**), or horizontal rules. Short paragraphs, blank lines
  between them, and simple "- " bullets or "label: value" lines. Emojis sparingly are fine.
- Target 120-250 words unless the question genuinely needs more. End with ONE natural follow-up
  offer when useful (e.g. "want the full buy/sell verdict?").

HONESTY:
- You are decision support, not SEBI-registered investment advice — the user knows this; don't lecture,
  but never promise returns or certainty. The system's own backtest shows ~52-57% win rates; edges are
  thin and probabilistic. When the honest answer is "no clear signal", say exactly that.
- The portfolio is PAPER money (virtual Rs 20L)."""


# ───────────────────────────── the agentic loop ─────────────────────────────
def chat(messages: list[dict]) -> dict:
    """messages: [{role: user|assistant, content: str}, ...] ending with the new user turn.
    Returns {reply, tools_used, tool_trace, usage, model}."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"reply": "Chat needs ANTHROPIC_API_KEY in .env — it's missing.", "tools_used": [],
                "tool_trace": [], "usage": None, "model": None}
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=1)

    convo = [{"role": m["role"], "content": m["content"]}
             for m in messages[-HISTORY_CAP:]
             if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
             and m["content"].strip()]
    if not convo or convo[-1]["role"] != "user":
        return {"reply": "Send a question to start.", "tools_used": [], "tool_trace": [],
                "usage": None, "model": MODEL}

    system = SYSTEM.replace("{today}", datetime.now().strftime("%A, %d %B %Y %H:%M IST"))
    trace, in_tok, out_tok = [], 0, 0
    resp = None
    try:
        for _ in range(MAX_STEPS):
            resp = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system,
                                          tools=TOOLS, messages=convo)
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            if resp.stop_reason != "tool_use":
                break
            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                args = block.input or {}
                try:
                    impl = TOOL_IMPLS.get(block.name)
                    out = impl(args) if impl else {"error": f"unknown tool {block.name}"}
                    ok = "error" not in out
                except Exception as e:
                    out, ok = {"error": f"{type(e).__name__}: {str(e)[:120]}"}, False
                trace.append({"tool": block.name, "input": args, "ok": ok})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str)[:6000]})
            convo.append({"role": "user", "content": results})
        reply = "".join(b.text for b in (resp.content if resp else []) if b.type == "text").strip()
        if not reply:
            reply = ("I gathered the data but ran out of reasoning steps — ask me to continue "
                     "or narrow the question.")
    except Exception as e:
        reply = f"The analyst hit an error: {str(e)[:160]}"
    return {"reply": reply, "tools_used": sorted({t["tool"] for t in trace}),
            "tool_trace": trace, "usage": {"in": in_tok, "out": out_tok}, "model": MODEL}


if __name__ == "__main__":          # smoke test:  python chat_analyst.py "your question"
    q = " ".join(sys.argv[1:]) or "What's the market regime right now?"
    out = chat([{"role": "user", "content": q}])
    print(f"\nTOOLS: {out['tools_used']}  ({out['usage']})\n\n{out['reply']}\n")
