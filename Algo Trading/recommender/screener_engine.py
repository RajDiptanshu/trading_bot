"""
screener_engine.py — scan a whole NSE index universe with the cheap technical funnel.

The funnel that makes "all of NSE" affordable:
  1. bulk-prefetch daily history for the scope in ONE threaded call (agents.prefetch_history),
  2. compute RS ranks cross-sectionally over the scope (agents.rs_ranks(symbols)),
  3. score every name in parallel with the pure-Python technical agent (NO Claude),
  4. overlay the pre-market News Brain sector bias (free — already computed),
  5. return a compact, sortable/filterable table.

Claude is deliberately kept OUT of the bulk scan — it only runs later on the handful of
candidates you (or Agent 4) drill into via /api/recommend. So scanning 200 or 500 names
costs ~zero API spend; only the funnel's survivors get the expensive treatment.

CLI:  python screener_engine.py nifty210                 # scan, print top 30 LONGs
      python screener_engine.py nifty210 IT              # filter to a sector
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import agents
import universe as uni

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _row(t: dict, entry: dict, brief: dict | None) -> dict:
    sector = entry.get("sector") or t.get("sector")
    sv = (brief.get("sector_view") or {}).get(sector, {}) if brief else {}
    sf = (brief.get("stock_flags") or {}).get(t["symbol"]) if brief else None
    return {
        "symbol": t["symbol"], "company": entry.get("company"), "sector": sector,
        "tier": entry.get("liquidity_tier"), "curated": entry.get("curated", False),
        "price": t["price"], "score": t["score"], "min_score": t["min_score_required"],
        "direction": t["direction"], "rs_rank": t["rs_rank"], "ret_12m": t.get("ret_12m_pct"),
        "rs_vs_nifty": t.get("rs_vs_nifty_pct"), "mom_12_1": t.get("mom_12_1_pct"),
        "atr_pct": t["atr_pct"], "adx": t.get("adx"), "rsi": t["rsi"],
        "pct_from_52w_high": t.get("pct_from_52w_high"), "n_confirmations": t.get("n_confirmations"),
        "breakout_volume": t.get("breakout_volume"), "liquidity_ok": t.get("liquidity_ok"),
        "turnover_cr": t.get("avg_traded_value_cr"),
        "news_sector_bias": sv.get("bias"), "news_stock_flag": (sf or {}).get("bias"),
    }


_SCAN_CACHE: dict = {}                                      # scope -> base scan result (+ ts)


def _scan_scope(scope: str, max_workers: int = 16) -> dict:
    """The expensive part: prefetch + universe RS + parallel technical scoring + news
    overlay. Returns ALL scored rows (unfiltered). Cached by scan_universe."""
    entries = uni.load_universe(scope)
    by_sym = {e["symbol"]: e for e in entries}
    syms = list(by_sym.keys())
    warmed = agents.prefetch_history(syms)
    rs_map = agents.rs_ranks(syms)                          # universe-relative RS
    try:
        import news_brain
        brief = news_brain.load_today_briefing()
    except Exception:
        brief = None

    def score_one(sym: str):
        try:
            t = agents.technical_agent(sym, rs_map=rs_map)
            return _row(t, by_sym[sym], brief) if "error" not in t else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        rows = [r for r in ex.map(score_one, syms) if r]
    return {"rows": rows, "universe_size": len(syms), "warmed": warmed,
            "errors": len(syms) - len(rows), "brief_date": brief.get("date") if brief else None}


def scan_universe(scope: str = "nifty210", min_score: int = 0, sector: str | None = None,
                  direction: str | None = None, liquid_only: bool = False,
                  limit: int | None = None, max_age: int = 600, refresh: bool = False) -> dict:
    """Ranked compact table for a scope. The expensive scan is cached per scope (~10 min),
    so filter/sort changes are instant. No Claude calls. NOTE first call per scope takes
    ~40s (prefetch + score the whole universe); subsequent calls are served from cache."""
    t0 = time.time()
    now = time.time()
    hit = _SCAN_CACHE.get(scope)
    is_cached = bool(hit and not refresh and now - hit["ts"] <= max_age)
    if not is_cached:
        base = _scan_scope(scope)
        base["ts"] = now
        _SCAN_CACHE[scope] = base
    else:
        base = hit

    rows = list(base["rows"])
    if sector:      rows = [r for r in rows if r["sector"] == sector]
    if direction:   rows = [r for r in rows if r["direction"] == direction]
    if liquid_only: rows = [r for r in rows if r["liquidity_ok"]]
    rows = [r for r in rows if r["score"] >= min_score]
    rows.sort(key=lambda r: (r["score"], r["rs_rank"]), reverse=True)
    if limit:
        rows = rows[:limit]
    return {"scope": scope, "universe_size": base["universe_size"],
            "scored_ok": base["universe_size"] - base["errors"], "errors": base["errors"],
            "warmed": base["warmed"], "returned": len(rows), "cached": is_cached,
            "elapsed_s": round(time.time() - t0, 1), "news_brief": base["brief_date"],
            "regime": agents.market_regime(), "rows": rows,
            "sectors": sorted({r["sector"] for r in base["rows"]})}


def main():
    scope = sys.argv[1] if len(sys.argv) > 1 else "nifty210"
    sector = sys.argv[2] if len(sys.argv) > 2 else None
    res = scan_universe(scope, sector=sector, direction="LONG", limit=30)
    print(f"\nSCREENER scope={res['scope']} | universe={res['universe_size']} "
          f"scored={res['scored_ok']} errors={res['errors']} warmed={res['warmed']} "
          f"| {res['elapsed_s']}s | news_brief={res['news_brief']}")
    reg = res["regime"]
    print(f"regime: {reg['label']} f1={reg['f1']} VIX={reg.get('vix')} ({reg.get('vix_tier')})  "
          f"min_score={reg['min_score']}\n")
    print(f"{'SYM':12} {'SECTOR':12} {'SCORE':>5} {'DIR':5} {'RS':>5} {'12m%':>6} {'ATR%':>5} {'NEWS':>9}")
    for r in res["rows"]:
        news = r.get("news_stock_flag") or r.get("news_sector_bias") or ""
        print(f"{r['symbol']:12} {r['sector'][:12]:12} {r['score']:>5} {r['direction'][:4]:5} "
              f"{r['rs_rank']:>5} {str(r.get('ret_12m')):>6} {r['atr_pct']:>5} {news:>9}")


if __name__ == "__main__":
    main()
