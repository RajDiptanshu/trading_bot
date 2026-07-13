"""
agent5_report.py — Agent 5: the daily performance report.

The reporter the desk reads after the close. It does NOT recompute the book — it
reuses agent4.summary() (equity, win-rate, profit-factor, max-DD, benchmark vs NIFTY,
open/closed trades with reasons, equity curve) and layers on what a performance review
needs:
  • realized vs unrealized P&L split + total
  • payoff ratio, average holding period, current drawdown
  • today's activity (entries + exits)
  • the GO-LIVE GATE scorecard (>=50 trades, PF>1.3, maxDD<10%, positive expectancy)
  • a Claude-written EOD narrative (desk note voice; graceful fallback with no API key)

Persists a dated snapshot to daily_reports/report_YYYY-MM-DD.json (+ report_latest.json),
so you build a history of how the book — and its honesty gates — evolve.

CLI:  python agent5_report.py build     # compute + Claude narrative + save
      python agent5_report.py show      # print the latest saved report
Schedule an EOD run (e.g. 15:50 IST, after Agent4_MonitorClose) to capture each day.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import agent4

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=True)
except Exception:
    pass

REPORT_DIR  = TRADING_BOT_DIR / "daily_reports"
LATEST_FILE = REPORT_DIR / "report_latest.json"
MODEL = os.getenv("REPORT_MODEL", os.getenv("STRATEGIST_MODEL", "claude-sonnet-4-6"))

# Go-live gates (SKILL.md §9): the measurable ones. >=3 months + every kill switch
# observed firing are tracked by hand; these four are computed each day.
def _gates(stats: dict, bench: dict | None) -> dict:
    tc   = stats.get("trades_closed") or 0
    pf   = stats.get("profit_factor")
    dd   = stats.get("max_drawdown_pct")
    ev   = stats.get("expectancy_inr")
    g = {
        "trades_>=50":        {"value": tc, "target": 50, "pass": tc >= 50},
        "profit_factor_>1.3": {"value": pf, "target": 1.3, "pass": pf is not None and pf > 1.3},
        "max_drawdown_<10%":  {"value": dd, "target": 10, "pass": dd is not None and dd < 10},
        "positive_expectancy":{"value": ev, "target": 0,  "pass": ev is not None and ev > 0},
    }
    g["_passed"] = sum(1 for k, v in g.items() if not k.startswith("_") and v["pass"])
    g["_total"]  = sum(1 for k in g if not k.startswith("_"))
    return g


def _today_activity(state: dict, today: str) -> dict:
    exits = [{"symbol": c["symbol"], "pnl": c["pnl"], "exit_reason": c.get("exit_reason"),
              "result": c.get("result")} for c in state["closed"] if c.get("exit_date") == today]
    entries = [{"symbol": p["symbol"], "instrument": p["instrument"], "entry_price": p["entry_price"],
                "qty": p.get("qty"), "reason": (p.get("reasoning") or "")[:120]}
               for p in state["positions"] if p.get("entry_date") == today]
    return {"entered": entries, "exited": exits,
            "realized_pnl": state.get("day", {}).get("realized_pnl", 0.0)}


def _narrative(rep: dict) -> str | None:
    """One Claude call — the desk's EOD note. Uses only supplied numbers."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                                     timeout=60.0, max_retries=1)
        payload = {k: rep[k] for k in ("date", "equity", "start_capital", "total_return_pct",
                                       "pnl", "halted", "option_funnel")}
        payload["books"] = {n: {k: b[k] for k in ("label", "equity", "start_capital",
                                                  "total_return_pct", "pnl", "stats", "benchmark",
                                                  "today", "gates", "halted",
                                                  "open_positions_brief")}
                            for n, b in rep["books"].items()}
        msg = client.messages.create(
            model=MODEL, max_tokens=750,
            system=("You are the desk's end-of-day performance reporter for an NSE paper-trading "
                    "operation running TWO SEPARATE books [V11]: an EQUITY-only book (Rs 10 lakh) "
                    "and an F&O-ONLY options book (Rs 10 lakh, defined-risk spreads, skips when a "
                    "structure can't be built — no equity fallback). Write a concise, honest EOD "
                    "note (<=200 words) from the supplied numbers ONLY — never invent a figure. "
                    "Cover EACH book briefly: how it did today and overall, P&L drivers, gate "
                    "progress; note the options funnel (signals vs entered vs skip reasons); alpha "
                    "vs NIFTY where present; and ONE thing to watch tomorrow. Plain, truthful, no "
                    "hype. This is paper trading."),
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
        return msg.content[0].text.strip()
    except Exception as e:
        return f"(narrative unavailable: {str(e)[:80]})"


def _capture_chains():
    """[V10.1 options focus] Daily chain snapshots at the EOD report run: indices + any
    open option positions (OPTIONS book [V11] — the equity book never holds options).
    Builds option_chain_history.jsonl (no free historical source exists). Best-effort —
    never blocks the report."""
    captured = []
    try:
        syms = {"NIFTY", "BANKNIFTY"}
        for p in agent4._load("options").get("positions", []):
            syms.add(p["symbol"])
        for s in syms:
            if agent4.fetch_option_chain(s):
                captured.append(s)
    except Exception:
        pass
    return captured


def _option_funnel(today: str) -> dict:
    """[V11] Aggregate today's OPTIONS-book decisions from the log: how many option
    signals were offered, how many entered, and every skip reason (the F&O-only book
    SKIPS instead of falling back, so skip reasons ARE the funnel). Legacy pre-split
    fallback_note lines are still counted if present."""
    out = {"signals": 0, "entered": 0, "skips": {}, "fallbacks": {}}
    try:
        with open(agent4.DECISIONS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not str(r.get("ts", "")).startswith(today):
                    continue
                fb = r.get("fallback_note")            # legacy single-book lines
                if fb and r.get("action_taken") == "ENTER":
                    out["fallbacks"][fb[:60]] = out["fallbacks"].get(fb[:60], 0) + 1
                if r.get("book") != "options" or r.get("cycle") != "entry":
                    continue
                if r.get("action_taken") == "ENTER":
                    out["signals"] += 1
                    out["entered"] += 1
                elif r.get("action_taken") == "SKIP" and r.get("skip_reason"):
                    out["signals"] += 1
                    key = r["skip_reason"][:60]
                    out["skips"][key] = out["skips"].get(key, 0) + 1
    except Exception:
        pass
    return out


def _book_report(name: str, bs: dict, state: dict, today: str) -> dict:
    """[V11] Per-book report block — same field shape the pre-split report used, so the
    UI/report consumers render each book with unchanged field names."""
    stats = bs.get("stats", {}) or {}
    realized = round(sum(c["pnl"] for c in state["closed"]), 2)
    unrealized = round(sum(r.get("unrealized_pnl", 0) for r in bs.get("open_positions", [])), 2)
    aw, al = stats.get("avg_win"), stats.get("avg_loss")
    payoff = round(aw / abs(al), 2) if aw and al else None
    held = [c.get("sessions_held") for c in state["closed"] if c.get("sessions_held") is not None]
    avg_hold = round(sum(held) / len(held), 1) if held else None
    eq, peak = bs["equity"], bs.get("peak_equity") or bs["start_capital"]
    cur_dd = round(max(0.0, (peak - eq) / peak * 100), 2) if peak else 0.0
    open_brief = [{"symbol": p["symbol"], "instrument": p.get("instrument"),
                   "unrealized_pnl": p.get("unrealized_pnl"), "sessions_held": p.get("sessions_held")}
                  for p in bs.get("open_positions", [])]
    return {
        "book": name, "label": bs.get("label"),
        "start_capital": bs["start_capital"], "equity": eq, "cash": bs["cash"],
        "peak_equity": peak, "total_return_pct": bs["return_pct"],
        "current_drawdown_pct": cur_dd, "halted": bs["halted"], "halt_reason": bs["halt_reason"],
        "pnl": {"realized": realized, "unrealized": unrealized,
                "total": round(realized + unrealized, 2)},
        "stats": {**stats, "payoff_ratio": payoff, "avg_holding_sessions": avg_hold},
        "benchmark": bs.get("benchmark"),
        "today": _today_activity(state, today),
        "gates": _gates(stats, bs.get("benchmark")),
        "open_positions": bs.get("open_positions", []),
        "open_positions_brief": open_brief,
        "closed_trades": bs.get("closed_trades", []),
        "equity_curve": bs.get("equity_curve", []),
        "limits": bs.get("limits", {}),
    }


def build_report(narrative: bool = True, persist: bool = True) -> dict:
    summ = agent4.summary()
    today = str(date.today())
    chains_captured = _capture_chains()

    books = {name: _book_report(name, summ["books"][name], agent4._load(name), today)
             for name in agent4.BOOKS}
    comb = summ["combined"]
    realized = round(sum(b["pnl"]["realized"] for b in books.values()), 2)
    unrealized = round(sum(b["pnl"]["unrealized"] for b in books.values()), 2)

    rep = {
        "date": today,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_version": "V11",
        # combined header (the UI's top strip); per-book truth lives in books.*
        "start_capital": comb["start_capital"], "equity": comb["equity"],
        "cash": comb["cash"], "total_return_pct": comb["return_pct"],
        "halted": bool(comb["halted_books"]),
        "halt_reason": ", ".join(comb["halted_books"]) or None,
        "pnl": {"realized": realized, "unrealized": unrealized,
                "total": round(realized + unrealized, 2)},
        "books": books,
        "option_funnel": _option_funnel(today),
        "chains_captured": chains_captured,
    }
    rep["narrative"] = _narrative(rep) if narrative else None

    if persist:
        REPORT_DIR.mkdir(exist_ok=True)
        out = REPORT_DIR / f"report_{today}.json"
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        LATEST_FILE.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return rep


def load_latest() -> dict | None:
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _print(r: dict):
    print(f"\n{'='*64}\n DAILY REPORT — {r['date']}  [V11 two books]\n{'='*64}")
    print(f" COMBINED Rs {r['equity']:,.0f}  ({r['total_return_pct']:+.2f}% vs Rs {r['start_capital']:,.0f})"
          f" | P&L real Rs {r['pnl']['realized']:+,.0f} / unreal Rs {r['pnl']['unrealized']:+,.0f}"
          + (" | ⚠ HALTED: " + r["halt_reason"] if r.get("halted") else ""))
    for name, b in r["books"].items():
        s, g, t = b["stats"], b["gates"], b["today"]
        print(f"\n --- {b.get('label', name.upper())} "
              f"(Rs {b['equity']:,.0f}, {b['total_return_pct']:+.2f}%)"
              + (" ⚠ HALTED: " + str(b["halt_reason"]) if b["halted"] else "") + " ---")
        print(f"  P&L real Rs {b['pnl']['realized']:+,.0f} | unreal Rs {b['pnl']['unrealized']:+,.0f}"
              f" | DD {b['current_drawdown_pct']}% (max {s.get('max_drawdown_pct')}%)")
        print(f"  Trades {s.get('trades_closed')} | win {s.get('win_rate')} | PF {s.get('profit_factor')}"
              f" | payoff {s.get('payoff_ratio')} | EV Rs {s.get('expectancy_inr')}"
              f" | hold {s.get('avg_holding_sessions')}d")
        bm = b.get("benchmark")
        if bm: print(f"  vs NIFTY: bot {bm['bot_return_pct']}% / nifty {bm['nifty_return_pct']}%"
                     f" = alpha {bm['alpha_pct']}%")
        print(f"  GATES {g['_passed']}/{g['_total']}: "
              + " ".join(f"[{'PASS' if v['pass'] else '----'}]{k}"
                         for k, v in g.items() if not k.startswith("_")))
        print(f"  TODAY: {len(t['entered'])} in / {len(t['exited'])} out"
              f" / realized Rs {t['realized_pnl']:+,.0f}")
    f = r.get("option_funnel") or {}
    if f.get("signals"):
        print(f"\n OPTION FUNNEL: {f['signals']} signals -> {f['entered']} entered"
              + (f" | skips: {f['skips']}" if f.get("skips") else ""))
    if r.get("narrative"):
        print(f"\n EOD NOTE:\n  {r['narrative']}")
    print()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "show":
        r = load_latest()
        print("no report saved yet — run: python agent5_report.py build" if not r else "")
        if r: _print(r)
    else:
        _print(build_report())


if __name__ == "__main__":
    main()
