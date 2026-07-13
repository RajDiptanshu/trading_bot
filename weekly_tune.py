"""
weekly_tune.py — the 4-week fine-tuning loop (V10.1, started 2026-07-06).

Every Sunday (scheduled 18:30, after ML_WeeklyRetrain at 18:00):
  1. refresh both backtest panels to the latest bars (live internet data),
  2. re-run the V10.1 config on both universes (curated 55 @ conf2; nifty210 @ conf3),
     full/IS/OOS — drift in these numbers is the early-warning signal,
  3. compare the live PAPER book's stats against the OOS backtest expectation,
  4. update strategy_memory.live_stats (feeds EV/Kelly sizing) ONLY when the paper book
     has >= 30 closed trades — small samples lie, so before that the backtest priors rule,
  5. append everything to backtest_results/tuning_log.jsonl (the 4-week review dataset).

REVIEW MILESTONE: 2026-08-03 (4 weeks). Judge V10.1 on: paper PF vs OOS-backtest PF,
option-funnel conversion, gate scorecard. Do NOT judge on <30 trades.
"""
import json, pickle, sys, warnings
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")

BOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "Algo Trading" / "recommender"))
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = BOT_DIR / "backtest_results"
TUNE_LOG = RESULTS / "tuning_log.jsonl"
MEMORY_FILE = BOT_DIR / "strategy_memory.json"
MIN_TRADES_FOR_LIVE_STATS = 30


def refresh_panels():
    import yfinance as yf
    import universe as uni
    # curated 55
    wl = json.load(open(BOT_DIR / "watchlist.json", encoding="utf-8"))["stocks"]
    wl = [s for s in wl if s.get("active", True)]
    meta55 = {s["symbol"]: {"tier": s.get("liquidity_tier", "MIDCAP"),
                            "sector": s.get("sector", "Other")} for s in wl}
    raw55 = yf.download([s + ".NS" for s in meta55] + ["^NSEI"], period="8y", interval="1d",
                        auto_adjust=True, group_by="ticker", progress=False, threads=True, timeout=40)
    pickle.dump({"raw": raw55, "meta": meta55}, open(RESULTS / "prices_v10.pkl", "wb"))
    # nifty210
    entries = uni.load_universe("nifty210")
    meta210 = {e["symbol"]: {"tier": e.get("liquidity_tier", "MIDCAP"),
                             "sector": e.get("sector", "Other")} for e in entries}
    raw210 = yf.download([s + ".NS" for s in meta210] + ["^NSEI"], period="8y", interval="1d",
                         auto_adjust=True, group_by="ticker", progress=False, threads=True, timeout=40)
    pickle.dump({"raw": raw210, "meta": meta210}, open(RESULTS / "prices_uni210.pkl", "wb"))
    print(f"refreshed panels: 55-name {raw55.shape}, 210-name {raw210.shape}")


def run_backtests():
    import backtest_lab as lab
    out = {}
    for cache, p in (("prices_v10.pkl", {}), ("prices_uni210.pkl", {"conf_min": 3})):
        panels, meta, S, thr, regime, f1, dates = lab.prep(cache)
        T = lab.run_param(panels, meta, S, thr, regime, f1, dates, {**lab.DEFAULTS, **p})
        blocks = lab.split_stats(T, cache, "2024-01-01")
        out[cache] = {k: {m: blocks[k].get(m) for m in
                          ("trades", "win_rate", "profit_factor", "expectancy", "max_dd_inr", "sharpe")}
                      for k in ("full", "IS", "OOS")}
        print(f"{cache}: OOS {lab.fmt(blocks['OOS'])}")
    return out


def paper_vs_backtest():
    import agent4
    # [V11 2026-07-12] two-book split: the backtests here are the EQUITY strategy
    # (V10.1 params on stock panels), so compare against the EQUITY book only.
    # live_stats (EV/Kelly prior) is likewise equity-strategy — options book excluded.
    s = agent4.summary()["books"]["equity"]
    st = s.get("stats", {}) or {}
    paper = {"trades_closed": st.get("trades_closed"), "win_rate": st.get("win_rate"),
             "profit_factor": st.get("profit_factor"), "expectancy_inr": st.get("expectancy_inr"),
             "max_dd_pct": st.get("max_drawdown_pct"), "equity": s.get("equity"),
             "alpha_vs_nifty": (s.get("benchmark") or {}).get("alpha_pct")}
    print(f"paper book (EQUITY): {paper}")
    # update live_stats only past the sample-size bar
    n = st.get("trades_closed") or 0
    updated = False
    if n >= MIN_TRADES_FOR_LIVE_STATS and st.get("win_rate") and st.get("avg_win") and st.get("avg_loss"):
        try:
            m = json.load(open(MEMORY_FILE, encoding="utf-8"))
            m["live_stats"] = {"win_rate": st["win_rate"],
                               "payoff_ratio": round(st["avg_win"] / abs(st["avg_loss"]), 2),
                               "as_of": str(date.today()), "n_trades": n}
            json.dump(m, open(MEMORY_FILE, "w", encoding="utf-8"), indent=2)
            updated = True
            print(f"live_stats updated from {n} paper trades (feeds EV/Kelly)")
        except Exception as e:
            print(f"live_stats update failed: {e}")
    else:
        print(f"live_stats NOT updated: {n} trades < {MIN_TRADES_FOR_LIVE_STATS} bar (backtest priors rule)")
    return paper, updated


def main():
    print(f"=== weekly tune {datetime.now():%Y-%m-%d %H:%M} ===")
    refresh_panels()
    bt = run_backtests()
    paper, updated = paper_vs_backtest()
    rec = {"date": str(date.today()), "backtests": bt, "paper": paper,
           "live_stats_updated": updated}
    with open(TUNE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(f"appended to {TUNE_LOG.name}; review milestone 2026-08-03")


if __name__ == "__main__":
    main()
