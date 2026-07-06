"""
universe.py — NSE index-constituent universe loader for the screener.

Lifts the bot off the hand-curated 55-name watchlist onto the real NSE indices
(Nifty 50 / Bank / Midcap 150 / Smallcap 250 / Nifty 500), fetched free from the
NSE archives mirror and cached to disk (constituents change only at index
rebalances, so a weekly refresh is plenty).

Merge rule (important): the curated watchlist.json WINS. The 55 names keep their
hand-assigned sector + angel_token + lot_size + liquidity_tier (so the existing
news contagion and live Agent 4 are unaffected). NEW names get a normalized
sector from the NSE 'Industry' column (with name heuristics to split the broad
'Financial Services' bucket into Banking/NBFC/Insurance/Exchange), lot_size 1
(equity-only until an F&O lot is added), and yfinance history (no angel_token).

CLI:  python universe.py refresh         # re-pull constituent CSVs from NSE
      python universe.py show nifty210    # list a scope
      python universe.py stats            # coverage + sector breakdown per scope
"""
from __future__ import annotations

import csv
import io
import json
import os
import ssl
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
WATCHLIST_FILE  = TRADING_BOT_DIR / "watchlist.json"
CACHE_DIR       = TRADING_BOT_DIR / "universe_cache"
MERGED_FILE     = CACHE_DIR / "universe.json"

NSE_ARCHIVE = "https://archives.nseindia.com/content/indices/"
INDEX_CSVS = {
    "nifty50":     "ind_nifty50list.csv",
    "niftybank":   "ind_niftybanklist.csv",
    "midcap150":   "ind_niftymidcap150list.csv",
    "smallcap250": "ind_niftysmallcap250list.csv",
    "nifty500":    "ind_nifty500list.csv",
}
# scope -> which indices make up the tradeable universe (union, de-duplicated)
SCOPES = {
    "core":     [],                                       # curated watchlist.json only
    "nifty50":  ["nifty50"],
    "nifty210": ["nifty50", "niftybank", "midcap150"],    # ~210, the validate-first scope
    "nifty500": ["nifty500"],
    "all":      ["nifty500", "smallcap250"],
}

# NSE macro 'Industry' -> our sector taxonomy (matches news_map.json keys where a
# contagion prior exists; unmapped ones fall back to per-stock news, which is fine).
INDUSTRY_SECTOR = {
    "Information Technology": "IT",
    "Fast Moving Consumer Goods": "FMCG",
    "Automobile and Auto Components": "Auto",
    "Oil Gas & Consumable Fuels": "Energy",
    "Power": "Power",
    "Telecommunication": "Telecom",
    "Construction Materials": "Cement",
    "Construction": "Infra",
    "Consumer Durables": "Consumer",
    "Consumer Services": "Consumer",
    "Metals & Mining": "Metals",
    "Healthcare": "Pharma",
    "Capital Goods": "Capital Goods",
    "Chemicals": "Chemicals",
    "Services": "Services",
    "Realty": "Realty",
    "Textiles": "Textiles",
    "Media Entertainment & Publication": "Media",
    "Diversified": "Conglomerate",
    "Financial Services": "Financials",        # refined by name heuristics below
}
_SSL = ssl.create_default_context()


def _normalize_sector(industry: str, name: str) -> str:
    base = INDUSTRY_SECTOR.get((industry or "").strip(), "Other")
    low = (name or "").lower()
    if base == "Financials":                   # split the 101-name bucket for contagion
        if "bank" in low:
            return "Banking"
        if any(w in low for w in ("insurance", "life insurance", "assurance", " gic", "lic ")):
            return "Insurance"
        if any(w in low for w in ("exchange", "depositor", "cams", "computer age", "central depository")):
            return "Exchange"
        if any(w in low for w in ("finance", "financial", "fin.", "capital", "housing",
                                  "investment", "fintech", "amc", "asset management", "wealth")):
            return "NBFC"
        return "NBFC"
    if base == "Metals" and "coal" in low:
        return "Mining"
    if base == "Pharma" and any(w in low for w in ("hospital", "healthcare", "diagnos", "labs", "wellness")):
        return "Healthcare"
    return base


# ───────────────────────────── fetch + cache ─────────────────────────────
def _http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return r.read().decode("utf-8", "replace")


def fetch_index(name: str, max_age_days: int = 7, force: bool = False) -> list[dict]:
    """Return [{symbol, company, industry}] for an index, disk-cached."""
    CACHE_DIR.mkdir(exist_ok=True)
    fn = CACHE_DIR / INDEX_CSVS[name]
    fresh = fn.exists() and (time.time() - fn.stat().st_mtime) < max_age_days * 86400
    if force or not fresh:
        try:
            body = _http_get(NSE_ARCHIVE + INDEX_CSVS[name])
            if "Symbol" in body.splitlines()[0]:
                fn.write_text(body, encoding="utf-8")
        except Exception:
            if not fn.exists():
                return []                          # no cache, fetch failed → empty
    try:
        rows = list(csv.DictReader(io.StringIO(fn.read_text(encoding="utf-8"))))
    except Exception:
        return []
    out = []
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        if sym and (r.get("Series") or "EQ").strip() in ("EQ", "BE", ""):
            out.append({"symbol": sym, "company": (r.get("Company Name") or "").strip(),
                        "industry": (r.get("Industry") or "").strip()})
    return out


# ───────────────────────────── merge ─────────────────────────────
def _curated() -> dict[str, dict]:
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            return {s["symbol"]: s for s in json.load(f)["stocks"]}
    except Exception:
        return {}


def _tier(indices: list[str]) -> str:
    if "nifty50" in indices: return "NIFTY50"
    if "niftybank" in indices: return "NIFTY50"
    if "midcap150" in indices: return "MIDCAP"
    if "smallcap250" in indices: return "SMALLCAP"
    return "NIFTY500"


def build_universe(force: bool = False) -> dict[str, dict]:
    """Fetch every index, tag membership, merge with curated watchlist, persist."""
    members: dict[str, dict] = {}                  # symbol -> {company, industry, indices:set}
    for name in INDEX_CSVS:
        for row in fetch_index(name, force=force):
            m = members.setdefault(row["symbol"], {"company": row["company"],
                                                   "industry": row["industry"], "indices": set()})
            m["indices"].add(name)
            if row["company"]:
                m["company"] = row["company"]
            if row["industry"]:
                m["industry"] = row["industry"]
    curated = _curated()
    universe: dict[str, dict] = {}
    for sym, m in members.items():
        indices = sorted(m["indices"])
        if sym in curated:                         # curated WINS (keep token/lot/sector)
            c = curated[sym]
            universe[sym] = {**c, "company": m["company"] or sym, "industry": m["industry"],
                             "indices": indices, "curated": True}
        else:
            universe[sym] = {"symbol": sym, "sector": _normalize_sector(m["industry"], m["company"]),
                             "angel_token": "", "lot_size": 1, "liquidity_tier": _tier(indices),
                             "active": True, "company": m["company"] or sym,
                             "industry": m["industry"], "indices": indices, "curated": False}
    # include curated names not in any fetched index (e.g. very small caps)
    for sym, c in curated.items():
        if sym not in universe:
            universe[sym] = {**c, "company": sym, "industry": c.get("sector", ""),
                             "indices": ["watchlist"], "curated": True}
    CACHE_DIR.mkdir(exist_ok=True)
    MERGED_FILE.write_text(json.dumps({"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "n": len(universe), "stocks": universe}, indent=1, ensure_ascii=False),
                           encoding="utf-8")
    return universe


def _load_merged(refresh: bool = False) -> dict[str, dict]:
    if refresh or not MERGED_FILE.exists():
        return build_universe(force=refresh)
    try:
        return json.loads(MERGED_FILE.read_text(encoding="utf-8"))["stocks"]
    except Exception:
        return build_universe()


def load_universe(scope: str = "nifty210", refresh: bool = False, active_only: bool = True) -> list[dict]:
    """List of universe entries for a scope. 'core' = curated watchlist only."""
    if scope == "core":
        return [s for s in _curated().values() if s.get("active", True) or not active_only]
    if scope not in SCOPES:
        scope = "nifty210"
    wanted = set(SCOPES[scope])
    merged = _load_merged(refresh)
    out = []
    for sym, e in merged.items():
        if wanted & set(e.get("indices", [])) and (e.get("active", True) or not active_only):
            out.append(e)
    out.sort(key=lambda e: (e["liquidity_tier"] != "NIFTY50", e["symbol"]))
    return out


# ───────────────────────────── CLI ─────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "refresh":
        u = build_universe(force=True)
        print(f"refreshed universe.json: {len(u)} symbols from NSE indices")
    elif cmd == "show":
        scope = sys.argv[2] if len(sys.argv) > 2 else "nifty210"
        rows = load_universe(scope)
        print(f"scope '{scope}': {len(rows)} symbols\n")
        for e in rows[:80]:
            print(f"  {e['symbol']:14} {e['sector']:14} {e['liquidity_tier']:9} {e.get('company','')[:34]}")
        if len(rows) > 80:
            print(f"  ... +{len(rows)-80} more")
    else:  # stats
        build_universe()
        for scope in ("core", "nifty50", "nifty210", "nifty500"):
            rows = load_universe(scope)
            sect = Counter(e["sector"] for e in rows)
            cur = sum(1 for e in rows if e.get("curated"))
            print(f"{scope:9}: {len(rows):3} symbols ({cur} curated) | "
                  f"top sectors: {', '.join(f'{s}:{n}' for s, n in sect.most_common(6))}")


if __name__ == "__main__":
    main()
