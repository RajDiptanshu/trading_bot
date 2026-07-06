"""
backtest_v10.py — validates the V10 agent logic against the V9 baseline.

Ablation design (so each change's contribution is visible, not just the bundle):
  A  V9 baseline          — as coded in morning_scan.py (replicates report 05)
  B  V9 + F1 hard veto    — report 05's recommended fix, isolated
  C  V9 + hybrid exits    — half off at 3*ATR -> breakeven, chandelier trail,
                            conditional 12-day time stop (exit-effect isolated)
  D  V10 entries, V9 exits— F1 veto + >=2/3 Kestner confirmations
                            + momentum-laggard veto (entry-effect isolated)
  E  V10 full             — D entries + C exits  (what agents.py now recommends)

Also counts (no trades): how often the FIXED mean-reversion rule fires
(RSI(2)<10, >MA200, ADX<32) vs the old dead rule (RSI14<35 AND >MA50).

No lookahead: signal on bar t close -> fill at bar t+1 open + tier slippage.
SL checked before target on the same bar (pessimistic). Costs from cost_model.py.
Caveats: survivorship bias (today's watchlist on its own past), yfinance data.
Treat RELATIVE comparisons as the signal, absolute numbers as optimistic.

Run:   python backtest_v10.py            (downloads ~8y dailies on first run,
                                          caches to backtest_results/prices_v10.pkl)
       python backtest_v10.py --refresh  (force re-download)
"""
import argparse, json, pickle, sys, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

BOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))
from cost_model import transaction_cost

RESULTS = BOT_DIR / "backtest_results"
RESULTS.mkdir(exist_ok=True)
CACHE = RESULTS / "prices_v10.pkl"

CAPITAL = 500_000; RISK = 20_000; MAX_POS = 3; TIME_STOP = 12
SLIP = {"NIFTY50": 0.001, "MIDCAP": 0.002, "SMALLCAP": 0.004}
GAP_CHASE = 0.015; GAP_DOWN = -0.05; ALLOC_CAP = 0.8 * CAPITAL; MAX_ALLOC_PER = 50_000
START = "2019-06-03"

# ───────────────────────── indicators ─────────────────────────
def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)

def atr_series(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx_series(df, n=14):
    up, dn = df.High.diff(), -df.Low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    atrn = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1/n, adjust=False).mean() / atrn
    mdi = 100 * mdm.ewm(alpha=1/n, adjust=False).mean() / atrn
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

# ───────────────────────── data ─────────────────────────
def load_data(refresh=False):
    if CACHE.exists() and not refresh:
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    import yfinance as yf
    with open(BOT_DIR / "watchlist.json", encoding="utf-8") as f:
        wl = [s for s in json.load(f)["stocks"] if s.get("active", True)]
    meta = {s["symbol"]: {"tier": s.get("liquidity_tier", "MIDCAP"),
                          "sector": s.get("sector", "Other")} for s in wl}
    tickers = [s["symbol"] + ".NS" for s in wl] + ["^NSEI"]
    print(f"downloading {len(tickers)} tickers, 8y daily...")
    raw = yf.download(tickers, period="8y", interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    with open(CACHE, "wb") as f:
        pickle.dump({"raw": raw, "meta": meta}, f)
    return {"raw": raw, "meta": meta}

def build_panels(raw):
    panels, rets12, rets12_1 = {}, {}, {}
    for t in raw.columns.get_level_values(0).unique():
        if not str(t).endswith(".NS"):
            continue
        sym = str(t)[:-3]
        df = raw[t].dropna(subset=["Close"]).copy()
        if len(df) < 300:
            continue
        c, v = df.Close, df.Volume
        df["ma20"] = c.rolling(20).mean(); df["ma50"] = c.rolling(50).mean()
        df["ma200"] = c.rolling(200).mean()
        df["a1"] = ((c > df.ma50) & (df.ma20 > df.ma50)) * 2
        df["a2"] = ((df.ma20 > df.ma20.shift(5)) & (df.ma50 > df.ma50.shift(20))) * 1
        df["rsi"] = rsi(c); df["c1"] = df.rsi.between(40, 65) * 1
        df["rsi2"] = rsi(c, 2)
        df["h52"] = df.High.rolling(252).max(); df["l52"] = df.Low.rolling(252).min()
        df["b2"] = (((df.h52 - c) / df.h52) <= 0.25) * 1; df["b3"] = (c >= 1.3 * df.l52) * 1
        vavg = v.rolling(20).mean(); df["d1"] = (v / vavg >= 1.0) * 1
        obv = (v * np.sign(c.diff()).fillna(0)).cumsum(); df["d2"] = (obv > obv.shift(10)) * 1
        bo = (df.High >= df.High.rolling(20).max()) & (v >= 1.5 * vavg)
        df["d3"] = (bo.rolling(5).max().fillna(0)) * 1
        pr = ((df.High - df.Low) / c).rolling(20).mean(); df["e1"] = pr.between(0.015, 0.055) * 1
        df["atr"] = atr_series(df)
        df["adx"] = adx_series(df)
        mid = c.rolling(20).mean(); sd = c.rolling(20).std()
        df["mr_old"] = (c <= (mid - 2 * sd) * 1.005) & (df.rsi < 35) & (c > df.ma50)
        df["mr_new"] = (df.rsi2 < 10) & (c > df.ma200) & (df.adx < 32)
        # Kestner confirmations (vectorised: bars since last 40d-high/-low event)
        n_idx = np.arange(len(df), dtype=float)
        new_hi = df.High >= df.High.rolling(40).max()
        new_lo = df.Low <= df.Low.rolling(40).min()
        hi_last = pd.Series(np.where(new_hi, n_idx, np.nan), index=df.index).ffill()
        lo_last = pd.Series(np.where(new_lo, n_idx, np.nan), index=df.index).ffill()
        df["conf_recency"] = hi_last >= lo_last
        r_now = df.High.rolling(10).max() - df.Low.rolling(10).min()
        df["conf_rangeexp"] = r_now > r_now.shift(20)
        df["mom_12_1"] = c.shift(21) / c.shift(252) - 1
        df["conf_mom"] = df.mom_12_1 > 0
        panels[sym] = df
        rets12[sym] = c / c.shift(252) - 1
    return panels, rets12

# ───────────────────────── engine ─────────────────────────
def run(panels, meta, S, thr, regime, f1, dates,
        f1_hard=False, v10_entries=False, hybrid_exits=False, costs_on=True):
    cash = 0; positions = []; trades = []
    rank_pct = None  # set by caller via S/aux
    for i, d in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        # ---- exits on bar d ----
        for p in positions[:]:
            sym = p["sym"]; df = panels[sym]
            if d not in df.index:
                continue
            row = df.loc[d]; p["days"] += 1
            p["hc"] = max(p["hc"], float(row.Close))
            p["hh"] = max(p["hh"], float(row.High))
            exits = []   # list of (qty, px, reason)
            stop = p["sl"]
            if hybrid_exits:
                chand = p["hc"] - 3 * float(row.atr) if not np.isnan(row.atr) else -np.inf
                if p["half_done"]:
                    stop = max(p["entry"], chand, p["sl"])      # BE + trail after half
                else:
                    stop = max(p["sl"], chand)                   # initial SL or trail
            if row.Open <= stop:
                exits.append((p["qty_open"], float(row.Open), "SL_GAP"))
            elif row.Low <= stop:
                exits.append((p["qty_open"], stop, "SL"))
            elif not hybrid_exits:
                if row.Open >= p["tg"]:
                    exits.append((p["qty_open"], float(row.Open), "TARGET_GAP"))
                elif row.High >= p["tg"]:
                    exits.append((p["qty_open"], p["tg"], "TARGET"))
                elif p["days"] >= TIME_STOP:
                    exits.append((p["qty_open"], float(row.Close), "TIME"))
            else:
                if not p["half_done"] and row.High >= p["tg"]:
                    px = max(float(row.Open), p["tg"]) if row.Open >= p["tg"] else p["tg"]
                    h = p["qty_open"] // 2
                    if h >= 1:
                        exits.append((h, px, "TARGET_HALF"))
                        p["half_done"] = True
                    else:
                        exits.append((p["qty_open"], px, "TARGET"))
                # conditional time stop: only if never reached +1*ATR
                if (p["days"] >= TIME_STOP and p["qty_open"] - sum(q for q, _, _ in exits) > 0
                        and not p["half_done"] and (p["hh"] - p["entry"]) < p["atr0"]):
                    exits.append((p["qty_open"] - sum(q for q, _, _ in exits), float(row.Close), "TIME_DEAD"))
            for q, px, reason in exits:
                if q < 1:
                    continue
                slip = SLIP[meta[sym]["tier"]]
                px_net = px * (1 - slip)
                cost = 0.0
                if costs_on:
                    # entry cost charged pro-rata per exited lot + exit leg cost
                    cost = (transaction_cost("EQUITY", "BUY", p["entry"], q)
                            + transaction_cost("EQUITY", "SELL", px_net, q))
                pnl = (px_net - p["entry"]) * q - cost
                trades.append({"sym": sym, "strat": p["strat"], "entry": p["entry"],
                               "entry_date": p["entry_date"], "exit": round(px_net, 2),
                               "exit_date": d.date(), "qty": q, "reason": reason,
                               "days": p["days"], "regime": p["regime"],
                               "costs": round(cost, 2), "pnl": round(pnl, 2)})
                p["qty_open"] -= q
                cash -= p["entry"] * q
            if p["qty_open"] < 1:
                positions.remove(p)
        # ---- entries decided at close of d, filled at open of nxt ----
        if d not in S.index:
            continue
        t_min = thr.get(d, 11)
        if f1_hard and not f1.get(d, False):
            continue                                            # F1 HARD VETO
        cands = []
        for sym in S.columns:
            if any(p["sym"] == sym for p in positions):
                continue
            sc = S.at[d, sym]
            df = panels[sym]
            if pd.isna(sc) or d not in df.index:
                continue
            row = df.loc[d]
            if sc < t_min:
                continue
            if pd.isna(row.atr) or row.atr <= 0:
                continue
            if v10_entries:
                n_conf = int(bool(row.conf_recency)) + int(bool(row.conf_rangeexp)) + int(bool(row.conf_mom))
                if n_conf < 2:
                    continue
                if row.get("rank_pct", 50) < 40:
                    continue                                    # momentum-laggard veto
            cands.append((float(sc), sym, row))
        cands.sort(reverse=True, key=lambda x: x[0])
        sec_open = {}
        for p in positions:
            sec_open[meta[p["sym"]]["sector"]] = sec_open.get(meta[p["sym"]]["sector"], 0) + 1
        for sc, sym, row in cands:
            if len(positions) >= MAX_POS:
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
            slip = SLIP[meta[sym]["tier"]]
            entry = o * (1 + slip)
            a = float(row.atr)
            qty = int(RISK / (2 * a))
            qty = min(qty, int(0.02 * CAPITAL / (0.20 * entry)), int(MAX_ALLOC_PER / entry))
            if qty < 1:
                continue
            val = qty * entry
            if cash + val > ALLOC_CAP:
                continue
            positions.append({"sym": sym, "strat": "MOM", "score": float(sc),
                              "entry": round(entry, 2), "entry_date": nxt.date(),
                              "sl": round(entry - 2 * a, 2), "tg": round(entry + 3 * a, 2),
                              "atr0": a, "qty": qty, "qty_open": qty, "val": val,
                              "days": 0, "hc": entry, "hh": entry, "half_done": False,
                              "regime": int(regime.get(d, 0))})
            cash += val
            sec_open[sec] = sec_open.get(sec, 0) + 1
    return pd.DataFrame(trades)

def stats(T, label):
    if T.empty:
        return {"label": label, "trades": 0}
    w, l = T[T.pnl > 0], T[T.pnl <= 0]
    daily = T.groupby("exit_date").pnl.sum().sort_index()
    eq = daily.cumsum()
    dd = float((eq - eq.cummax()).min())
    # Sharpe + K-ratio on the daily P&L curve (Kestner ch4)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() else None
    n = len(eq)
    if n > 10:
        x = np.arange(n, dtype=float)
        b1, b0 = np.polyfit(x, eq.values, 1)
        resid = eq.values - (b0 + b1 * x)
        se = float(np.sqrt((resid ** 2).sum() / (n - 2)) / np.sqrt(((x - x.mean()) ** 2).sum()))
        kratio = float(b1 / (se * n)) if se else None
    else:
        kratio = None
    yrs = {str(y): round(g.pnl.sum(), 0) for y, g in T.groupby(pd.to_datetime(T.exit_date).dt.year)}
    return {"label": label, "trades": int(len(T)),
            "win_rate": round(len(w) / len(T) * 100, 1),
            "expectancy": round(float(T.pnl.mean()), 0),
            "profit_factor": round(float(w.pnl.sum() / abs(l.pnl.sum())), 2) if len(l) and l.pnl.sum() else None,
            "total_pnl": round(float(T.pnl.sum()), 0),
            "total_costs": round(float(T.costs.sum()), 0),
            "max_dd_inr": round(dd, 0), "return_pct": round(float(T.pnl.sum()) / CAPITAL * 100, 1),
            "sharpe": round(sharpe, 2) if sharpe else None,
            "k_ratio": round(kratio, 3) if kratio is not None else None,
            "by_year": yrs,
            "by_reason": T.groupby("reason").pnl.agg(["count", "sum"]).round(0).to_dict("index")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    data = load_data(args.refresh)
    raw, meta = data["raw"], data["meta"]
    panels, rets12 = build_panels(raw)
    meta = {k: v for k, v in meta.items() if k in panels}
    print(f"{len(panels)} symbols with >=300 bars")

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

    # MR sleeve fire counts (no trades) — proves the fix is alive
    mr_old = int(sum(df.mr_old.loc[df.index >= START].sum() for df in panels.values()))
    mr_new = int(sum(df.mr_new.loc[df.index >= START].sum() for df in panels.values()))
    days_n = sum(int((df.index >= START).sum()) for df in panels.values())
    print(f"MR sleeve fires over {days_n} stock-days: OLD rule {mr_old}  |  NEW rule {mr_new}")

    variants = {
        "A_v9_baseline":      dict(f1_hard=False, v10_entries=False, hybrid_exits=False),
        "B_f1_veto_only":     dict(f1_hard=True,  v10_entries=False, hybrid_exits=False),
        "C_hybrid_exits_only":dict(f1_hard=False, v10_entries=False, hybrid_exits=True),
        "D_v10_entries_only": dict(f1_hard=True,  v10_entries=True,  hybrid_exits=False),
        "E_v10_full":         dict(f1_hard=True,  v10_entries=True,  hybrid_exits=True),
    }
    out, all_trades = {"mr_fires": {"old": mr_old, "new": mr_new, "stock_days": days_n}}, {}
    for name, kw in variants.items():
        T = run(panels, meta, S, thr, regime, f1, dates, costs_on=True, **kw)
        out[name] = stats(T, name)
        all_trades[name] = T
        s = out[name]
        print(f"\n{name}: trades {s.get('trades')} | win {s.get('win_rate')}% | "
              f"PF {s.get('profit_factor')} | exp Rs{s.get('expectancy')} | "
              f"P&L Rs{s.get('total_pnl')} | DD Rs{s.get('max_dd_inr')} | "
              f"Sharpe {s.get('sharpe')} | K {s.get('k_ratio')}")
    with open(RESULTS / "v10_summary.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    all_trades["E_v10_full"].to_csv(RESULTS / "v10_trades.csv", index=False)
    print(f"\nwrote {RESULTS / 'v10_summary.json'} and v10_trades.csv")
    print("Reminder: relative comparisons are the signal; absolute numbers are "
          "flattered by survivorship. K-ratio/Sharpe (Kestner) > profit factor.")

if __name__ == "__main__":
    main()
