"""
news_brain.py — Agent 6: the pre-market News & Macro Brain.

Runs before the 09:15 IST open. Reads the last 24h of news across three layers
(global/macro · sector · per-stock), then makes ONE Claude call that READS the
headlines through the contagion playbook (news_map.json) and emits a structured
briefing: a market bias, per-sector bias with reasons, and per-stock flags.

This is the missing piece behind the user's Accenture example: when Accenture
guides down, the brain flags the whole NSE IT sector NEGATIVE *before the open*,
and that view is injected into the Claude strategist (Agent 3) so every
recommendation that day is coloured by the news — not just per-symbol keyword counts.

Design:
  * Sources come from news_sources.py (free now, paid-pluggable later).
  * Contagion logic is data-driven (news_map.json) — edit the map, not the code.
  * Claude path is primary; a deterministic keyword matcher is the no-API-key fallback.
  * Output cached to news_briefs/news_brief_YYYY-MM-DD.json (+ latest.json).

CLI:  python news_brain.py build      # fetch + synthesise + save today's briefing
      python news_brain.py show       # print the latest saved briefing
      python news_brain.py dry        # fetch only, no Claude (free; shows raw feed)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import news_sources as ns

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))

# Load .env (ANTHROPIC_API_KEY etc.) the same way app.py / agent4.py do — this is a
# standalone entry point, so nothing else loads it for us.
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=True)
except Exception:
    pass

WATCHLIST_FILE  = TRADING_BOT_DIR / "watchlist.json"
NEWS_MAP_FILE   = TRADING_BOT_DIR / "news_map.json"
BRIEF_DIR       = TRADING_BOT_DIR / "news_briefs"
LATEST_BRIEF    = BRIEF_DIR / "latest.json"

LOOKBACK_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
MODEL = os.getenv("NEWS_BRAIN_MODEL", os.getenv("STRATEGIST_MODEL", "claude-sonnet-4-6"))
MAX_HEADLINES_TO_LLM = 110

# Non-obvious company aliases for headline→symbol attribution (the deterministic
# fallback needs these; Claude mostly knows them but they sharpen attribution).
NAME_ALIASES: dict[str, list[str]] = {
    "RELIANCE": ["reliance", "ril", "jio"], "HDFCBANK": ["hdfc bank"],
    "ICICIBANK": ["icici bank"], "INFY": ["infosys"], "TCS": ["tata consultancy", "tcs"],
    "AXISBANK": ["axis bank"], "SBIN": ["sbi", "state bank of india"], "KOTAKBANK": ["kotak"],
    "BAJFINANCE": ["bajaj finance"], "BAJAJFINSV": ["bajaj finserv"], "SBILIFE": ["sbi life"],
    "HDFCLIFE": ["hdfc life"], "BHARTIARTL": ["bharti airtel", "airtel"], "HINDALCO": ["hindalco"],
    "TATASTEEL": ["tata steel"], "COALINDIA": ["coal india"], "SUNPHARMA": ["sun pharma"],
    "DRREDDY": ["dr reddy", "dr. reddy"], "MARUTI": ["maruti"], "BAJAJ-AUTO": ["bajaj auto"],
    "HEROMOTOCO": ["hero motocorp", "hero moto"], "EICHERMOT": ["eicher", "royal enfield"],
    "HCLTECH": ["hcl tech", "hcltech"], "TECHM": ["tech mahindra"], "WIPRO": ["wipro"],
    "TITAN": ["titan"], "ASIANPAINT": ["asian paints"], "HINDUNILVR": ["hindustan unilever", "hul"],
    "ITC": ["itc"], "NESTLEIND": ["nestle"], "BRITANNIA": ["britannia"],
    "APOLLOHOSP": ["apollo hospital", "apollo hospitals"], "LT": ["larsen", "l&t", "l & t"],
    "ADANIPORTS": ["adani ports"], "ADANIENT": ["adani enterprises", "adani group"],
    "GRASIM": ["grasim"], "ULTRACEMCO": ["ultratech"], "BPCL": ["bharat petroleum", "bpcl"],
    "POWERGRID": ["power grid", "powergrid"], "BEL": ["bharat electronics"],
    "SHRIRAMFIN": ["shriram finance"], "INDUSINDBK": ["indusind"], "POWERINDIA": ["hitachi energy"],
    "MCX": ["mcx", "multi commodity"], "BSE": ["bse ", "bombay stock exchange"],
    "HAL": ["hindustan aeronautics", "hal "], "DIXON": ["dixon"], "NETWEB": ["netweb"],
    "HINDZINC": ["hindustan zinc"], "DSSL": ["dynacons"], "DATAPATTNS": ["data patterns"],
    "HFCL": ["hfcl"], "VEDL": ["vedanta"], "DCXINDIA": ["dcx"], "GRAVITA": ["gravita"],
}


# ───────────────────────────── config loaders ─────────────────────────────
def load_watchlist() -> list[dict]:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return [s for s in json.load(f)["stocks"] if s.get("active", True)]


def load_news_map() -> dict:
    try:
        with open(NEWS_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sectors": {}, "macro_drivers": {}}


def sector_index(wl: list[dict]) -> tuple[dict[str, list[str]], dict[str, str]]:
    by_sector: dict[str, list[str]] = {}
    by_symbol: dict[str, str] = {}
    for s in wl:
        by_sector.setdefault(s["sector"], []).append(s["symbol"])
        by_symbol[s["symbol"]] = s["sector"]
    return by_sector, by_symbol


# ───────────────────────────── collection ─────────────────────────────
def collect_news(symbols: list[str], hours: int = LOOKBACK_HOURS,
                 use_gdelt: bool = True) -> list[ns.NewsItem]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    agg = ns.NewsAggregator(ns.default_sources(symbols, gdelt=use_gdelt))
    return agg.collect(since)


def _attribute_symbol(title: str, by_symbol: dict[str, str]) -> str | None:
    """Best-effort headline→symbol match via aliases (deterministic path)."""
    low = title.lower()
    for sym in by_symbol:
        for alias in NAME_ALIASES.get(sym, [sym.lower()]):
            if alias in low:
                return sym
    return None


# ───────────────────────────── deterministic fallback ─────────────────────────────
def _deterministic_briefing(items: list[ns.NewsItem], news_map: dict,
                            by_sector: dict[str, list[str]], by_symbol: dict[str, str]) -> dict:
    """No-API-key path: keyword-match the contagion cues. Coarse but honest."""
    titles = [it.title.lower() for it in items]
    blob = " || ".join(titles)
    sector_view = {}
    for sector, spec in news_map.get("sectors", {}).items():
        if sector not in by_sector:
            continue
        neg = [c for c in spec.get("negative_cues", []) if c in blob]
        pos = [c for c in spec.get("positive_cues", []) if c in blob]
        net = len(pos) - len(neg)
        if net == 0 and not (neg or pos):
            continue
        bias = "NEGATIVE" if net < 0 else "POSITIVE" if net > 0 else "MIXED"
        cue = (neg or pos or ["-"])[0]
        ex = next((it.title for it in items if cue in it.title.lower()), "")
        sector_view[sector] = {"bias": bias, "confidence": round(min(0.3 + 0.12 * abs(net), 0.75), 2),
                               "reason": f"keyword cue '{cue}' matched ({len(pos)}+/{len(neg)}-)",
                               "key_headline": ex[:140]}
    # macro/market bias from a few strong signals
    risk_off = sum(1 for k in ("crude surge", "fii selling", "selloff", "sell-off", "crash",
                               "yields spike", "war", "tariff") if k in blob)
    risk_on = sum(1 for k in ("rally", "record high", "inflows", "rate cut", "stimulus") if k in blob)
    neg_sectors = sum(1 for v in sector_view.values() if v["bias"] == "NEGATIVE")
    pos_sectors = sum(1 for v in sector_view.values() if v["bias"] == "POSITIVE")
    mb = ("RISK_OFF" if risk_off > risk_on or neg_sectors > pos_sectors + 1
          else "RISK_ON" if risk_on > risk_off and pos_sectors >= neg_sectors else "NEUTRAL")
    # Propagate sector bias to its stocks (contagion), keyed off any company headline.
    # Skip neutral-sector names entirely so the fallback surfaces signal, not noise.
    stock_flags = {}
    for it in items:
        if it.category != "company":
            continue
        sym = it.symbol or _attribute_symbol(it.title, by_symbol)
        if not sym or sym in stock_flags:
            continue
        sec = by_symbol.get(sym)
        sv = sector_view.get(sec or "", {})
        if sv.get("bias") in ("NEGATIVE", "POSITIVE"):
            stock_flags[sym] = {"bias": sv["bias"],
                                "reason": f"{sec} sector {sv['bias'].lower()} (contagion)",
                                "headline": it.title[:140], "source": "sector"}
    return _wrap_briefing(items, "deterministic-fallback", mb,
                          "keyword/contagion heuristic (no ANTHROPIC_API_KEY)",
                          [], sector_view, stock_flags,
                          f"Deterministic scan: {neg_sectors} sector(s) negative, {pos_sectors} positive; "
                          f"market tone {mb}. Enable Claude for real news reasoning.")


# ───────────────────────────── Claude synthesis ─────────────────────────────
def _compact_map(news_map: dict, by_sector: dict[str, list[str]]) -> dict:
    """Send Claude only the thesis+bellwethers for sectors we actually hold."""
    out = {}
    for sector, spec in news_map.get("sectors", {}).items():
        if sector in by_sector:
            out[sector] = {"symbols": by_sector[sector],
                           "bellwethers": spec.get("bellwethers", []),
                           "thesis": spec.get("thesis", "")}
    return out


def _select_headlines(items: list[ns.NewsItem]) -> list[ns.NewsItem]:
    """Keep all company + global/macro items; cap noisy 'markets' chatter by recency."""
    keep = [it for it in items if it.category in ("company", "global", "macro")]
    markets = [it for it in items if it.category not in ("company", "global", "macro")]
    keep += markets[: max(0, MAX_HEADLINES_TO_LLM - len(keep))]
    return keep[:MAX_HEADLINES_TO_LLM]


def _claude_briefing(items: list[ns.NewsItem], news_map: dict,
                     by_sector: dict[str, list[str]], by_symbol: dict[str, str]) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=90.0, max_retries=1)
    selected = _select_headlines(items)
    lines = []
    for it in selected:
        age = it.age_hours()
        tag = it.symbol or it.category
        extra = f" — {it.summary[:160]}" if it.summary else ""   # RSS body curbs misreads
        lines.append(f"[{tag}|{age if age is not None else '?'}h|{it.source[:12]}] {it.title}{extra}")
    headlines_block = "\n".join(lines)

    macro = {k: v.get("logic", "") for k, v in news_map.get("macro_drivers", {}).items()}
    schema = {
        "market_bias": "RISK_ON | NEUTRAL | RISK_OFF",
        "market_bias_reason": "<=30 words citing the overnight/global drivers",
        "global_cues": [{"headline": "verbatim or tight paraphrase", "implication": "what it means for NSE",
                         "affects": ["SECTOR"]}],
        "sector_view": {"SECTOR_NAME": {"bias": "POSITIVE|NEGATIVE|NEUTRAL", "confidence": 0.0,
                                        "reason": "<=30 words, cite the headline/driver",
                                        "key_headline": "the headline that drives this"}},
        "stock_flags": {"SYMBOL": {"bias": "POSITIVE|NEGATIVE|NEUTRAL", "reason": "<=25 words",
                                   "headline": "the driving headline", "source": "direct|sector"}},
        "summary": "<=80 words, the pre-market brief a desk head would read aloud",
    }
    system = (
        "You are the pre-market news analyst for an NSE (Indian equities) swing-trading desk. "
        "It is before the 09:15 IST open. You are given the last 24h of headlines (global, macro, "
        "sector, and per-company), a contagion playbook mapping global bellwethers to NSE sectors, "
        "and the desk's watchlist. Produce a structured briefing.\n\n"
        "RULES:\n"
        "- READ the actual headlines. Only assign a sector or stock bias when a headline (or a clear "
        "contagion link in the playbook) supports it. When there is no signal, say NEUTRAL — do not invent one.\n"
        "- CONTAGION IS THE POINT: connect global/bellwether news to NSE sectors using the playbook. "
        "Example: a weak Accenture print or 'IT spending slowdown' ⇒ NSE IT sector NEGATIVE (INFY/TCS/HCLTECH/"
        "TECHM/WIPRO), even with no India-specific headline. Crude spikes ⇒ OMC/BPCL negative. China stimulus ⇒ metals positive.\n"
        "- confidence (0-1) reflects evidence strength: a direct company print ~0.8; a pure cross-read contagion ~0.4-0.6.\n"
        "- BE TERSE so your reply fits. stock_flags: include ONLY symbols with a clear, specific catalyst "
        "(usually < 12 names) — do NOT list every watchlist symbol, and skip NEUTRAL/no-news names. "
        "sector_view: only sectors with a real signal. Keep every 'reason' under 22 words.\n"
        "- STAY LITERAL. Base every claim on what a headline (or its summary) explicitly says. Do NOT infer "
        "unstated specifics: a debt/ECB/bond fundraise is NOT equity dilution; a shared facility is not one "
        "company's raise; never assume a number, a 'miss', or 'guidance' not printed. If a headline is "
        "ambiguous or you lack the article body, keep confidence <= 0.5 and describe only the literal headline.\n"
        "- Never fabricate a headline or a number. Output ONLY the JSON object, matching the schema exactly."
    )
    user = (f"DATE: {datetime.now().strftime('%Y-%m-%d')} (pre-open)\n\n"
            f"CONTAGION PLAYBOOK (sector ⇒ symbols, bellwethers, thesis):\n{json.dumps(_compact_map(news_map, by_sector), indent=1)}\n\n"
            f"MACRO DRIVERS:\n{json.dumps(macro, indent=1)}\n\n"
            f"WATCHLIST (symbol ⇒ sector):\n{json.dumps(by_symbol)}\n\n"
            f"HEADLINES (last 24h; tag = symbol or category):\n{headlines_block}\n\n"
            f"SCHEMA:\n{json.dumps(schema, indent=1)}\n\nReturn ONLY the JSON object.")

    msg = client.messages.create(model=MODEL, max_tokens=4096, system=system,
                                 messages=[{"role": "user", "content": user}])
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    return _wrap_briefing(items, "claude", data.get("market_bias", "NEUTRAL"),
                          data.get("market_bias_reason", ""), data.get("global_cues", []),
                          data.get("sector_view", {}), data.get("stock_flags", {}),
                          data.get("summary", ""),
                          tokens={"in": msg.usage.input_tokens, "out": msg.usage.output_tokens})


# ───────────────────────────── assembly + persistence ─────────────────────────────
def _wrap_briefing(items, engine, market_bias, mb_reason, global_cues,
                   sector_view, stock_flags, summary, tokens=None) -> dict:
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": engine, "model": MODEL if engine == "claude" else None,
        "lookback_hours": LOOKBACK_HOURS, "n_headlines": len(items),
        "market_bias": market_bias, "market_bias_reason": mb_reason,
        "global_cues": global_cues, "sector_view": sector_view, "stock_flags": stock_flags,
        "summary": summary, "tokens": tokens,
        "sources_used": sorted({it.source.split(":")[0] for it in items}),
        # full audit trail: every claim above must trace to one of these fetched headlines
        "source_headlines": [it.as_dict() for it in items[:160]],
    }


def build_briefing(hours: int = LOOKBACK_HOURS, use_llm: bool = True,
                   use_gdelt: bool = True, persist: bool = True) -> dict:
    wl = load_watchlist()
    by_sector, by_symbol = sector_index(wl)
    news_map = load_news_map()
    items = collect_news([s["symbol"] for s in wl], hours, use_gdelt=use_gdelt)
    if use_llm and os.getenv("ANTHROPIC_API_KEY"):
        try:
            brief = _claude_briefing(items, news_map, by_sector, by_symbol)
        except Exception as e:
            brief = _deterministic_briefing(items, news_map, by_sector, by_symbol)
            brief["engine"] = f"deterministic-fallback (claude error: {str(e)[:90]})"
    else:
        brief = _deterministic_briefing(items, news_map, by_sector, by_symbol)
    if persist:
        BRIEF_DIR.mkdir(exist_ok=True)
        out = BRIEF_DIR / f"news_brief_{brief['date']}.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)                                   # atomic
        LATEST_BRIEF.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    return brief


# ───────────────────────────── consumption API (for Agent 3) ─────────────────────────────
def load_today_briefing(max_age_hours: int = 18) -> dict | None:
    """Return today's briefing if fresh enough, else None (strategist degrades silently)."""
    try:
        brief = json.loads(LATEST_BRIEF.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        gen = datetime.fromisoformat(brief["generated_utc"])
        if (datetime.now(timezone.utc) - gen).total_seconds() > max_age_hours * 3600:
            return None
    except Exception:
        return None
    return brief


def news_context_for(symbol: str, sector: str | None, brief: dict | None) -> dict | None:
    """Compact, strategist-ready slice of the briefing for one symbol."""
    if not brief:
        return None
    sv = (brief.get("sector_view") or {}).get(sector or "", {})
    sf = (brief.get("stock_flags") or {}).get(symbol)
    if not sv and not sf and brief.get("market_bias") in (None, "NEUTRAL"):
        return None
    return {"as_of": brief.get("date"), "market_bias": brief.get("market_bias"),
            "market_bias_reason": brief.get("market_bias_reason"),
            "sector": sector, "sector_bias": sv.get("bias"), "sector_confidence": sv.get("confidence"),
            "sector_reason": sv.get("reason"), "stock_flag": sf}


# ───────────────────────────── CLI ─────────────────────────────
def _print_brief(b: dict):
    print(f"\n{'='*70}\n PRE-MARKET BRIEF — {b['date']}  [{b['engine']}]  "
          f"market_bias={b['market_bias']}\n{'='*70}")
    print(f" {b.get('market_bias_reason','')}")
    print(f" headlines scanned: {b['n_headlines']} | sources: {', '.join(b.get('sources_used', []))}")
    if b.get("tokens"): print(f" tokens: {b['tokens']}")
    print("\n SECTOR VIEW:")
    for sec, v in sorted(b.get("sector_view", {}).items(), key=lambda kv: kv[1].get("bias", "")):
        print(f"   {v.get('bias','?'):9} ({v.get('confidence','?')})  {sec:20} {v.get('reason','')[:70]}")
    if b.get("stock_flags"):
        print("\n STOCK FLAGS:")
        for sym, v in b["stock_flags"].items():
            print(f"   {v.get('bias','?'):9} {sym:12} {v.get('reason','')[:70]}")
    print(f"\n SUMMARY: {b.get('summary','')}\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "show":
        b = load_today_briefing(max_age_hours=999) or {}
        if not b: print("no briefing saved yet — run: python news_brain.py build")
        else: _print_brief(b)
    elif cmd == "dry":
        wl = load_watchlist()
        items = collect_news([s["symbol"] for s in wl], LOOKBACK_HOURS)
        print(f"DRY RUN — {len(items)} unique headlines in last {LOOKBACK_HOURS}h "
              f"(no Claude call, nothing saved):\n")
        for it in items[:40]:
            print(f"  [{it.category:8}] {it.source[:14]:14} {it.title[:84]}")
    else:
        b = build_briefing()
        _print_brief(b)


if __name__ == "__main__":
    main()
