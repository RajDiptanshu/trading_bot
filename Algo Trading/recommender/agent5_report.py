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
                                       "pnl", "stats", "benchmark", "today", "gates", "halted",
                                       "open_positions_brief")}
        msg = client.messages.create(
            model=MODEL, max_tokens=650,
            system=("You are the desk's end-of-day performance reporter for an NSE paper-trading "
                    "book (Rs 20 lakh start). Write a concise, honest EOD note (<=160 words) from "
                    "the supplied numbers ONLY — never invent a figure. Cover: how the book did "
                    "today and overall, P&L drivers, alpha vs NIFTY, the go-live gate progress, and "
                    "ONE thing to watch tomorrow. Plain, truthful, no hype. This is paper trading."),
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
        return msg.content[0].text.strip()
    except Exception as e:
        return f"(narrative unavailable: {str(e)[:80]})"


def _capture_chains():
    """[V10.1 options focus] Daily chain snapshots at the EOD report run: indices + any
    open option positions. Builds option_chain_history.jsonl (no free historical source
    exists). Best-effort — never blocks the report."""
    captured = []
    try:
        syms = {"NIFTY", "BANKNIFTY"}
        for p in agent4._load().get("positions", []):
            if p.get("instrument") != "EQUITY":
                syms.add(p["symbol"])
        for s in syms:
            if agent4.fetch_option_chain(s):
                captured.append(s)
    except Exception:
        pass
    return captured


def _option_funnel(today: str) -> dict:
    """Aggregate today's option-vs-equity routing decisions from the decision log:
    how many entries proposed options, how many took them, and every fallback reason.
    This is the tuning dataset for the options-focus month."""
    out = {"entries": 0, "took_option": 0, "fallbacks": {}}
    try:
        with open(agent4.DECISIONS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not str(r.get("ts", "")).startswith(today) or r.get("action_taken") != "ENTER":
                    continue
                out["entries"] += 1
                if (r.get("instrument") or "EQUITY") != "EQUITY":
                    out["took_option"] += 1
                fb = r.get("fallback_note")
                if fb:
                    key = fb[:60]
                    out["fallbacks"][key] = out["fallbacks"].get(key, 0) + 1
    except Exception:
        pass
    return out


def build_report(narrative: bool = True, persist: bool = True) -> dict:
    summ = agent4.summary()
    state = agent4._load()
    today = str(date.today())
    stats = summ.get("stats", {}) or {}
    chains_captured = _capture_chains()

    realized = round(sum(c["pnl"] for c in state["closed"]), 2)
    unrealized = round(sum(r.get("unrealized_pnl", 0) for r in summ.get("open_positions", [])), 2)
    aw, al = stats.get("avg_win"), stats.get("avg_loss")
    payoff = round(aw / abs(al), 2) if aw and al else None
    held = [c.get("sessions_held") for c in state["closed"] if c.get("sessions_held") is not None]
    avg_hold = round(sum(held) / len(held), 1) if held else None
    eq, peak = summ["equity"], summ.get("peak_equity") or summ["start_capital"]
    cur_dd = round(max(0.0, (peak - eq) / peak * 100), 2) if peak else 0.0

    open_brief = [{"symbol": p["symbol"], "instrument": p.get("instrument"),
                   "unrealized_pnl": p.get("unrealized_pnl"), "sessions_held": p.get("sessions_held")}
                  for p in summ.get("open_positions", [])]

    rep = {
        "date": today,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_capital": summ["start_capital"], "equity": eq, "cash": summ["cash"],
        "peak_equity": peak, "total_return_pct": summ["return_pct"],
        "current_drawdown_pct": cur_dd, "halted": summ["halted"], "halt_reason": summ["halt_reason"],
        "pnl": {"realized": realized, "unrealized": unrealized,
                "total": round(realized + unrealized, 2)},
        "stats": {**stats, "payoff_ratio": payoff, "avg_holding_sessions": avg_hold},
        "benchmark": summ.get("benchmark"),
        "today": _today_activity(state, today),
        "option_funnel": _option_funnel(today),
        "chains_captured": chains_captured,
        "gates": _gates(stats, summ.get("benchmark")),
        "open_positions": summ.get("open_positions", []),
        "open_positions_brief": open_brief,
        "closed_trades": summ.get("closed_trades", []),
        "equity_curve": summ.get("equity_curve", []),
        "limits": summ.get("limits", {}),
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
    g = r["gates"]
    print(f"\n{'='*64}\n DAILY REPORT — {r['date']}\n{'='*64}")
    print(f" Equity Rs {r['equity']:,.0f}  ({r['total_return_pct']:+.2f}% vs Rs {r['start_capital']:,.0f} start)"
          f"  | drawdown {r['current_drawdown_pct']}% (max {r['stats'].get('max_drawdown_pct')}%)")
    print(f" P&L  realized Rs {r['pnl']['realized']:+,.0f} | unrealized Rs {r['pnl']['unrealized']:+,.0f}"
          f" | total Rs {r['pnl']['total']:+,.0f}")
    s = r["stats"]
    print(f" Trades {s.get('trades_closed')} | win {s.get('win_rate')} | PF {s.get('profit_factor')}"
          f" | payoff {s.get('payoff_ratio')} | EV Rs {s.get('expectancy_inr')} | hold {s.get('avg_holding_sessions')}d")
    b = r.get("benchmark")
    if b: print(f" vs NIFTY: bot {b['bot_return_pct']}% / nifty {b['nifty_return_pct']}% = alpha {b['alpha_pct']}%")
    print(f" GO-LIVE GATES {g['_passed']}/{g['_total']} passed:")
    for k, v in g.items():
        if not k.startswith("_"):
            print(f"   [{'PASS' if v['pass'] else '----'}] {k:22} value={v['value']} target {v['target']}")
    t = r["today"]
    print(f" TODAY: {len(t['entered'])} entered, {len(t['exited'])} exited, realized Rs {t['realized_pnl']:+,.0f}")
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
