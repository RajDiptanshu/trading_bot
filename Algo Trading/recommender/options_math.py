"""
options_math.py — Black-Scholes implied vol + Greeks, pure stdlib (no scipy/numpy).

Angel One's option chain returns a live premium (LTP) but NO implied vol or Greeks;
the unofficial NSE endpoint returns IV in percent but still no Greeks. This module
inverts Black-Scholes from the live premium to recover IV, then computes
delta / gamma / theta / vega. It exists to:
  • enrich the option chain (agent4.fetch_option_chain) so every leg carries iv+delta,
  • let the executor pick the long CALL leg by DELTA (~0.65, ITM) instead of ATM strike
    — ITM cuts theta while keeping defined risk,
  • grow option_chain_history.jsonl into an IV/Greeks dataset that later validates
    per-stock IV-percentile gating (SKILL.md §5c / §9 roadmap).

Conventions: S spot, K strike, T years to expiry, r annualised risk-free (decimal),
sigma annualised implied vol (decimal). European options; dividend yield ignored
(q=0) — a standard simplification acceptable for a paper book. Greeks are returned
in trader units: theta per CALENDAR day, vega per 1 vol point (1%).
"""
from __future__ import annotations
import math
from datetime import date as _date, datetime as _dt

RISK_FREE_RATE = 0.065                 # India ~10Y G-sec; override per-call via r=
_SQRT_2PI = math.sqrt(2 * math.pi)
_MIN_T = 1.0 / 365.0                   # floor time-to-expiry at one day
_SIG_LO, _SIG_HI = 1e-4, 5.0           # implied-vol search bounds (0.01% .. 500%)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bs_price(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> float:
    """European option price. kind in ('ce','pe'). Falls back to intrinsic at the
    degenerate zero-vol / zero-time corner so bisection stays well-defined."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return max(S - K, 0.0) if kind == "ce" else max(K - S, 0.0)
    disc = math.exp(-r * T)
    if kind == "ce":
        return S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    return K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def greeks(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> dict:
    """delta, gamma, theta (per calendar day), vega (per 1 vol point). {} if inputs
    are degenerate."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return {}
    disc = math.exp(-r * T)
    pdf = _norm_pdf(d1)
    sqrtT = math.sqrt(T)
    if kind == "ce":
        delta = _norm_cdf(d1)
        theta = (-S * pdf * sigma / (2 * sqrtT)) - r * K * disc * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-S * pdf * sigma / (2 * sqrtT)) + r * K * disc * _norm_cdf(-d2)
    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega / 100.0}


def implied_vol(price, S: float, K: float, T: float, r: float, kind: str,
                tol: float = 1e-5, max_iter: int = 60):
    """Invert Black-Scholes for sigma from a market premium. Newton (seeded by the
    Brenner-Subrahmanyam ATM approximation) with a bracketed bisection fallback.
    Returns None when there is no sensible solution — non-positive/undefined inputs,
    or a price outside the no-arbitrage band (a premium that is all intrinsic value
    carries no vol information)."""
    if price is None or S <= 0 or K <= 0 or T <= 0:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    disc = math.exp(-r * T)
    if kind == "ce":
        lower, upper = max(S - K * disc, 0.0), S
    else:
        lower, upper = max(K * disc - S, 0.0), K * disc
    # a price at/below discounted intrinsic (or at/above the upper bound) is pure
    # intrinsic / arbitrage — vol is indeterminate there.
    if price <= lower + 1e-7 or price >= upper - 1e-7:
        return None

    sigma = min(max(math.sqrt(2 * math.pi / T) * price / S, 0.05), 3.0)  # ATM seed
    for _ in range(max_iter):
        d1, d2 = _d1_d2(S, K, T, r, sigma)
        if d1 is None:
            break
        model = (S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)) if kind == "ce" \
            else (K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1))
        diff = model - price
        if abs(diff) < tol:
            return sigma if _SIG_LO < sigma < _SIG_HI else None
        vega = S * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-8:
            break
        sigma -= diff / vega
        if not (_SIG_LO < sigma < _SIG_HI):
            break

    lo, hi = _SIG_LO, _SIG_HI                       # bracketed bisection fallback
    flo = bs_price(S, K, T, r, lo, kind) - price
    fhi = bs_price(S, K, T, r, hi, kind) - price
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if flo * fhi > 0:
        return None                                 # root not bracketed
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        fm = bs_price(S, K, T, r, mid, kind) - price
        if abs(fm) < tol:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def _resolve_iv(node: dict, S: float, K: float, T: float, r: float, kind: str):
    """In-house IV from the node's premium; if that is indeterminate, fall back to a
    source-provided IV (NSE gives percent, Angel gives None) normalised to a decimal."""
    iv = implied_vol(node.get("ltp"), S, K, T, r, kind)
    if iv:
        return iv
    src = node.get("iv")
    if src in (None, "", 0):
        return None
    try:
        src = float(src)
    except (TypeError, ValueError):
        return None
    if src <= 0:
        return None
    return src / 100.0 if src > 3 else src           # >3 ⇒ almost certainly a percent


def enrich_chain(chain: dict, spot: float, r: float = RISK_FREE_RATE, today=None) -> dict:
    """Fill iv + delta/gamma/theta/vega on every ce/pe node of a
    {expiry_str: {strike: {ce/pe: node}}} chain, IN PLACE. Best-effort: a node that
    cannot be solved is left with iv=None and no Greeks. Expiry strings parse as
    '%d-%b-%Y' (the shape both live_quotes and the NSE fallback emit). Returns the
    same dict for convenience."""
    if not chain or not spot or spot <= 0:
        return chain
    today = today or _date.today()
    for exp_str, book in chain.items():
        try:
            exp = _dt.strptime(exp_str, "%d-%b-%Y").date()
            T = max((exp - today).days, 1) / 365.0
        except (ValueError, TypeError):
            continue
        for strike, slot in book.items():
            if not isinstance(slot, dict):
                continue
            try:
                K = float(strike)
            except (TypeError, ValueError):
                continue
            for leg in ("ce", "pe"):
                node = slot.get(leg)
                if not isinstance(node, dict):
                    continue
                iv = _resolve_iv(node, float(spot), K, T, r, leg)
                if not iv:
                    node["iv"] = None
                    continue
                node["iv"] = round(iv, 4)
                g = greeks(float(spot), K, T, r, iv, leg)
                if g:
                    node["delta"] = round(g["delta"], 4)
                    node["gamma"] = round(g["gamma"], 6)
                    node["theta"] = round(g["theta"], 4)
                    node["vega"] = round(g["vega"], 4)
    return chain


if __name__ == "__main__":                          # sanity self-test (no network)
    S, K, T, r = 100.0, 100.0, 30 / 365, 0.065
    print("round-trip IV recovery + ATM Greeks (true sigma = 0.20):")
    for kind in ("ce", "pe"):
        px = bs_price(S, K, T, r, 0.20, kind)
        iv = implied_vol(px, S, K, T, r, kind)
        g = greeks(S, K, T, r, iv, kind)
        print(f"  {kind}: price={px:7.3f}  iv={iv:.4f}  delta={g['delta']:+.3f}  "
              f"theta/day={g['theta']:+.4f}  vega={g['vega']:.4f}")
    print("\ndelta by strike (spot=100, sigma=0.20, 30d) — 0.65-delta call is ITM:")
    demo = {"30-Jul-2026": {k * 1.0: {"ce": {"ltp": bs_price(S, float(k), T, r, 0.20, "ce")},
                                      "pe": {"ltp": bs_price(S, float(k), T, r, 0.20, "pe")}}
                            for k in range(90, 112, 2)}}
    enrich_chain(demo, S, r=r, today=_date(2026, 6, 30))
    for strike, slot in sorted(demo["30-Jul-2026"].items()):
        d = slot["ce"].get("delta")
        tag = "  <- ~0.65 (ITM long leg)" if d and abs(d - 0.65) < 0.06 else ""
        print(f"  K={strike:6.1f}  call delta={d}{tag}")
