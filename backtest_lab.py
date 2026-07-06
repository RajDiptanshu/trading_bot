"""
backtest_lab.py — the improvement loop's engine (2026-07-06).

Parameterized re-implementation of backtest_v10's E_v10_full runner so rule variants
and universes can be tested honestly:

  * same no-lookahead discipline: signal at close(t) -> fill at open(t+1) + tier slippage
  * same NSE cost model both sides, SL-before-target pessimism, gap handling, sector cap
  * NEW: parameterized conf_min / rank_min / stop / target / trail / time_stop / max_pos
  * NEW: --cache to run the same rules on a different universe (55 curated vs nifty210)
  * NEW: in-sample / out-of-sample split (--oos 2024-01-01): variants are RANKED on IS,
    the winner is judged ONLY on OOS. Guards against picking curve-fit noise.

Usage:
  python backtest_lab.py baseline                      # E_v10_full defaults, full period
  python backtest_lab.py baseline --cache prices_uni210.pkl
  python backtest_lab.py grid                          # variant grid, IS/OOS report
"""
import argparse, json, sys, pickle, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

BOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))
from cost_model import transaction_cost
import backtest_v10 as bt

RESULTS = BOT_DIR / "backtest_results"
SLIP = bt.SLIP; GAP_CHASE = bt.GAP_CHASE; GAP_DOWN = bt.GAP_DOWN
CAPITAL = bt.CAPITAL; RISK = bt.RISK
ALLOC_CAP = bt.ALLOC_CAP; MAX_ALLOC_PER = bt.MAX_ALLOC_PER
START = bt.START

# V10.1 live config (2026-07-06): stop 2.5*ATR + day-8 time stop, both validated OOS.
# (conf_min=2 = curated-watchlist config; universe runs should pass conf_min=3 — the
# tiered gate agent4 now applies to non-curated names.)
DEFAULTS = dict(conf_min=2, rank_min=40, stop_mult=2.5, tgt_mult=3.0,
                trail_mult=3.0, time_stop=8, max_pos=3)


def run_param(panels, meta, S, thr, regime, f1, dates, p=DEFAULTS, costs_on=True):
    """E_v10_full engine with parameterized rules (f1 hard veto + confs + hybrid exits)."""
    cash = 0; positions = []; trades = []
    for i, d in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        for pos in positions[:]:
            sym = pos["sym"]; df = panels[sym]
            if d not in df.index:
                continue
            row = df.loc[d]; pos["days"] += 1
            pos["hc"] = max(pos["hc"], float(row.Close))
            pos["hh"] = max(pos["hh"], float(row.High))
            exits = []
            chand = pos["hc"] - p["trail_mult"] * float(row.atr) if not np.isnan(row.atr) else -np.inf
            stop = max(pos["entry"], chand, pos["sl"]) if pos["half_done"] else max(pos["sl"], chand)
            if row.Open <= stop:
                exits.append((pos["qty_open"], float(row.Open), "SL_GAP"))
            elif row.Low <= stop:
                exits.append((pos["qty_open"], stop, "SL"))
            else:
                if not pos["half_done"] and row.High >= pos["tg"]:
                    px = max(float(row.Open), pos["tg"]) if row.Open >= pos["tg"] else pos["tg"]
                    h = pos["qty_open"] // 2
                    if h >= 1:
                        exits.append((h, px, "TARGET_HALF")); pos["half_done"] = True
                    else:
                        exits.append((pos["qty_open"], px, "TARGET"))
                if (pos["days"] >= p["time_stop"] and pos["qty_open"] - sum(q for q, _, _ in exits) > 0
                        and not pos["half_done"] and (pos["hh"] - pos["entry"]) < pos["atr0"]):
                    exits.append((pos["qty_open"] - sum(q for q, _, _ in exits), float(row.Close), "TIME_DEAD"))
            for q, px, reason in exits:
                if q < 1:
                    continue
                slip = SLIP[meta[sym]["tier"]]
                px_net = px * (1 - slip)
                cost = (transaction_cost("EQUITY", "BUY", pos["entry"], q)
                        + transaction_cost("EQUITY", "SELL", px_net, q)) if costs_on else 0.0
                trades.append({"sym": sym, "entry": pos["entry"], "entry_date": pos["entry_date"],
                               "exit": round(px_net, 2), "exit_date": d.date(), "qty": q,
                               "reason": reason, "days": pos["days"], "costs": round(cost, 2),
                               "pnl": round((px_net - pos["entry"]) * q - cost, 2)})
                pos["qty_open"] -= q
                cash -= pos["entry"] * q
            if pos["qty_open"] < 1:
                positions.remove(pos)
        if d not in S.index:
            continue
        t_min = thr.get(d, 11)
        if not f1.get(d, False):
            continue                                            # F1 hard veto (validated)
        cands = []
        for sym in S.columns:
            if any(x["sym"] == sym for x in positions):
                continue
            sc = S.at[d, sym]
            df = panels[sym]
            if pd.isna(sc) or d not in df.index or sc < t_min:
                continue
            row = df.loc[d]
            if pd.isna(row.atr) or row.atr <= 0:
                continue
            n_conf = int(bool(row.conf_recency)) + int(bool(row.conf_rangeexp)) + int(bool(row.conf_mom))
            if n_conf < p["conf_min"]:
                continue
            if row.get("rank_pct", 50) < p["rank_min"]:
                continue
            cands.append((float(sc), sym, row))
        cands.sort(reverse=True, key=lambda x: x[0])
        sec_open = {}
        for x in positions:
            sec_open[meta[x["sym"]]["sector"]] = sec_open.get(meta[x["sym"]]["sector"], 0) + 1
        for sc, sym, row in cands:
            if len(positions) >= p["max_pos"]:
                break
            sec = meta[sym]["sector"]
            if sec_open.get(sec, 0) >= 1:
                continue
            df = panels[sym]
            if nxt not in df.index:
                continue
            o = float(df.at[nxt, "Open"]); cl = float(row.Close)
            gap = (o - cl) / cl
            if gap > GAP_CHASE or gap < GAP_DOWN:
                continue
            entry = o * (1 + SLIP[meta[sym]["tier"]])
            a = float(row.atr)
            qty = int(RISK / (p["stop_mult"] * a))
            qty = min(qty, int(0.02 * CAPITAL / (0.20 * entry)), int(MAX_ALLOC_PER / entry))
            if qty < 1:
                continue
            val = qty * entry
            if cash + val > ALLOC_CAP:
                continue
            positions.append({"sym": sym, "entry": round(entry, 2), "entry_date": nxt.date(),
                              "sl": round(entry - p["stop_mult"] * a, 2),
                              "tg": round(entry + p["tgt_mult"] * a, 2),
                              "atr0": a, "qty_open": qty, "days": 0,
                              "hc": entry, "hh": entry, "half_done": False})
            cash += val
            sec_open[sec] = sec_open.get(sec, 0) + 1
    return pd.DataFrame(trades)


def prep(cache_name: str):
    """Load a price cache and build everything run_param needs."""
    with open(RESULTS / cache_name, "rb") as f:
        data = pickle.load(f)
    raw, meta = data["raw"], data["meta"]
    panels, rets12 = bt.build_panels(raw)
    meta = {k: v for k, v in meta.items() if k in panels}
    R = pd.DataFrame(rets12)
    rank_pct = R.rank(axis=1, pct=True) * 100
    b1 = pd.DataFrame(np.select([rank_pct >= 75, rank_pct >= 50], [2, 1], 0),
                      index=R.index, columns=R.columns)
    for s, df in panels.items():
        df["rank_pct"] = rank_pct[s].reindex(df.index)
    nif = raw["^NSEI"].dropna(subset=["Close"])
    nma50 = nif.Close.rolling(50).mean(); nma200 = nif.Close.rolling(200).mean()
    f1 = (nif.Close > nma50)
    regime = (f1 * 1 + ((nif.Close > nma200) & (nma200 > nma200.shift(20))) * 1)
    thr = regime.map({0: 11, 1: 10, 2: 9})
    score = {}
    for s, df in panels.items():
        sc = (df.a1 + df.a2 + df.b2 + df.b3 + b1[s].reindex(df.index).fillna(0)
              + df.c1 + df.d1 + df.d2 + df.d3 + df.e1).astype(float)
        score[s] = sc + regime.reindex(df.index).fillna(0)
    S = pd.DataFrame(score)
    dates = [d for d in nif.index if d >= pd.Timestamp(START)]
    return panels, meta, S, thr, regime, f1, dates


def split_stats(T: pd.DataFrame, label: str, oos_start: str):
    """Full/IS/OOS stat blocks for one trade table."""
    T = T.copy(); T["exit_date"] = pd.to_datetime(T["exit_date"])
    blocks = {}
    for name, sub in (("full", T), ("IS", T[T.exit_date < oos_start]), ("OOS", T[T.exit_date >= oos_start])):
        sub2 = sub.copy(); sub2["exit_date"] = sub2["exit_date"].dt.date
        blocks[name] = bt.stats(sub2, f"{label}/{name}")
    return blocks


def fmt(s):
    return (f"trades {s.get('trades',0):4} | win {s.get('win_rate','—')}% | PF {s.get('profit_factor','—')} "
            f"| exp Rs{s.get('expectancy','—')} | DD Rs{s.get('max_dd_inr','—')} | Sharpe {s.get('sharpe','—')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["baseline", "grid"])
    ap.add_argument("--cache", default="prices_v10.pkl")
    ap.add_argument("--oos", default="2024-01-01")
    args = ap.parse_args()
    panels, meta, S, thr, regime, f1, dates = prep(args.cache)
    print(f"cache={args.cache} | {len(panels)} symbols | {dates[0].date()} .. {dates[-1].date()}")

    if args.mode == "baseline":
        T = run_param(panels, meta, S, thr, regime, f1, dates, DEFAULTS)
        blocks = split_stats(T, "baseline", args.oos)
        for k in ("full", "IS", "OOS"):
            print(f"  {k:4}: {fmt(blocks[k])}")
        out = RESULTS / f"lab_baseline_{args.cache.replace('.pkl','')}.json"
        out.write_text(json.dumps(blocks, indent=1, default=str), encoding="utf-8")
        print("wrote", out.name)
        return

    # grid: one-at-a-time deviations from DEFAULTS (robustness, not combinatorial fishing)
    grid = {
        "base(conf2,rank40,trail3,ts12)": {},
        "conf3":        {"conf_min": 3},
        "rank50":       {"rank_min": 50},
        "rank30":       {"rank_min": 30},
        "trail2.5":     {"trail_mult": 2.5},
        "trail3.5":     {"trail_mult": 3.5},
        "ts8":          {"time_stop": 8},
        "ts16":         {"time_stop": 16},
        "tgt2.5":       {"tgt_mult": 2.5},
        "tgt3.5":       {"tgt_mult": 3.5},
        "stop1.5":      {"stop_mult": 1.5},
        "stop2.5":      {"stop_mult": 2.5},
        "maxpos5":      {"max_pos": 5},
    }
    report = {}
    for name, dev in grid.items():
        p = {**DEFAULTS, **dev}
        T = run_param(panels, meta, S, thr, regime, f1, dates, p)
        report[name] = split_stats(T, name, args.oos)
        is_, oos = report[name]["IS"], report[name]["OOS"]
        print(f"{name:30} IS: {fmt(is_)}")
        print(f"{'':30} OOS: {fmt(oos)}")
    out = RESULTS / f"lab_grid_{args.cache.replace('.pkl','')}.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", out.name)


if __name__ == "__main__":
    main()
