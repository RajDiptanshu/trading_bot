"""
backtest_uni_diag.py — WHY does the broad universe underperform the curated 55?
(2026-07-06 loop iteration 2b)

Diagnosis on prices_uni210 (same no-lookahead engine as backtest_lab):
  1. base run -> trade breakdown by liquidity tier / sector / symbol-curated-flag
  2. targeted fixes, one at a time:
       tierN50   — trade only NIFTY50-tier names from the broad universe
       rank60    — tighten momentum-laggard veto to RS>=60 (stronger cross-section)
       conf3     — require all 3 Kestner confirmations
       thr+1     — raise the min composite score by 1 everywhere
       curated   — only curated watchlist names (control: should approach PF 2.38)
Each reported full/IS/OOS. Verdict = which (if any) gate makes 200 names tradeable.
"""
import json, sys, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

BOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))
import backtest_lab as lab
import backtest_v10 as bt

OOS = "2024-01-01"


def main():
    panels, meta, S, thr, regime, f1, dates = lab.prep("prices_uni210.pkl")
    print(f"{len(panels)} symbols | {dates[0].date()}..{dates[-1].date()}")
    import json as _j
    curated = set()
    try:
        wl = _j.load(open(BOT_DIR / "watchlist.json", encoding="utf-8"))["stocks"]
        curated = {s["symbol"] for s in wl if s.get("active", True)}
    except Exception:
        pass

    # 1) base run + breakdowns
    T = lab.run_param(panels, meta, S, thr, regime, f1, dates, lab.DEFAULTS)
    T["tier"] = T.sym.map(lambda s: meta[s]["tier"])
    T["sector"] = T.sym.map(lambda s: meta[s]["sector"])
    T["curated"] = T.sym.isin(curated)
    print("\nBASE on uni210:", lab.fmt(bt.stats(T, "base")))
    print("\nby TIER:")
    print(T.groupby("tier").pnl.agg(["count", "sum", "mean"]).round(0))
    print("\nby CURATED flag:")
    print(T.groupby("curated").pnl.agg(["count", "sum", "mean"]).round(0))
    print("\nworst 8 symbols:")
    print(T.groupby("sym").pnl.sum().sort_values().head(8).round(0))
    print("\nbest 8 symbols:")
    print(T.groupby("sym").pnl.sum().sort_values().tail(8).round(0))

    # 2) targeted fixes
    def run_filtered(name, keep_syms=None, p=None, thr_shift=0):
        pp = {**lab.DEFAULTS, **(p or {})}
        S2 = S[[c for c in S.columns if keep_syms is None or c in keep_syms]]
        thr2 = (thr + thr_shift) if thr_shift else thr
        T2 = lab.run_param(panels, meta, S2, thr2, regime, f1, dates, pp)
        blocks = lab.split_stats(T2, name, OOS)
        print(f"\n{name}")
        for k in ("full", "IS", "OOS"):
            print(f"  {k:4}: {lab.fmt(blocks[k])}")
        return blocks

    out = {}
    n50 = {s for s, m in meta.items() if m["tier"] == "NIFTY50"}
    out["tierN50"] = run_filtered("tierN50 (broad universe, NIFTY50-tier only)", keep_syms=n50)
    out["rank60"] = run_filtered("rank60 (RS veto 40->60)", p={"rank_min": 60})
    out["conf3"] = run_filtered("conf3 (need all 3 confirmations)", p={"conf_min": 3})
    out["thr+1"] = run_filtered("thr+1 (min score +1)", thr_shift=1)
    out["curated_only"] = run_filtered("curated names only (control)", keep_syms=curated)
    (BOT_DIR / "backtest_results" / "uni_diag.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("\nwrote backtest_results/uni_diag.json")


if __name__ == "__main__":
    main()
