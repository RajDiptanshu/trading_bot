"""
COST MODEL — Indian market transaction costs (NSE)
====================================================
All rates are NAMED CONSTANTS defined at the top.
Edit the constants here; the rest of the codebase picks up the change.

!! VERIFY all rates against current SEBI / NSE / BSE circulars before use !!
NSE rate card: https://www.nseindia.com/regulations/member-sops-handling-investor-grievances
SEBI fee schedule: https://www.sebi.gov.in/legal/circulars/
"""

from __future__ import annotations

# ── EQUITY DELIVERY ─────────────────────────────────────────────────────────
# VERIFY against current SEBI/exchange rules
BROKERAGE_EQUITY_FLAT    = 20.0         # Rs per executed order (flat cap)
BROKERAGE_EQUITY_RATE    = 0.0003       # 0.03% of turnover (whichever is LOWER than flat)

STT_EQUITY_RATE          = 0.001        # 0.1% — on BOTH buy and sell sides (delivery)
                                        # VERIFY: intraday STT differs (0.025% sell only)

EXC_TXN_EQUITY_RATE      = 0.0000345   # 0.00345% of turnover — NSE equity segment
                                        # VERIFY against NSE circular; BSE rate differs

SEBI_TURNOVER_FEE_RATE   = 0.000001    # Rs 10 per crore = 0.000001 of turnover
                                        # VERIFY: SEBI revises this periodically

STAMP_DUTY_EQUITY_RATE   = 0.00015     # 0.015% — BUY side only (delivery)
                                        # VERIFY: state-specific caps may apply

GST_RATE                 = 0.18        # 18% — applied on (brokerage + exchange txn charge)
                                        # VERIFY: GST applies to services, not STT/stamp duty

# ── OPTIONS (NSE F&O) ────────────────────────────────────────────────────────
# VERIFY against current SEBI/exchange rules
BROKERAGE_OPTIONS_FLAT   = 20.0        # Rs per order — flat (most discount brokers)

STT_OPTIONS_SELL_RATE    = 0.000625    # 0.0625% of premium — SELL side ONLY
                                        # VERIFY: STT on options is on sell side of premium

EXC_TXN_OPTIONS_RATE     = 0.00053     # 0.053% of premium turnover — NSE F&O segment
                                        # VERIFY: NSE revises this; BSE F&O rate differs

# SEBI_TURNOVER_FEE_RATE shared with equity (same rate, same formula)

STAMP_DUTY_OPTIONS_RATE  = 0.00003     # 0.003% of premium — BUY side only
                                        # VERIFY: per Finance Act 2020 schedule

# GST_RATE shared with equity (18%)


# ── CORE FUNCTION ────────────────────────────────────────────────────────────

def transaction_cost(
    segment:  str,
    side:     str,
    price:    float,
    quantity: int,
    lot_size: int = 1,
) -> float:
    """
    Returns the total transaction cost in INR for ONE side of a trade.
    Call once for entry and once for exit; sum both for the round-trip cost.

    Parameters
    ----------
    segment  : "EQUITY" or "OPTIONS"
    side     : "BUY" or "SELL"
    price    : For EQUITY  — stock price per share (Rs).
               For OPTIONS — option premium per share, NOT per lot (Rs).
    quantity : For EQUITY  — number of shares.
               For OPTIONS — number of lots (lot_size converts to total units).
    lot_size : Options contract lot size (ignored for EQUITY — leave at default 1).

    Returns
    -------
    float : Total charges in Rs for this side of the trade.

    Notes
    -----
    - STT for EQUITY delivery is charged on BOTH sides.
    - STT for OPTIONS is charged on the SELL side only (on premium).
    - Stamp duty is charged on the BUY side only in both segments.
    - GST applies to brokerage + exchange transaction charge only,
      NOT to STT or stamp duty.
    - Brokerage for EQUITY is min(flat, percentage); OPTIONS is flat only.
    """
    segment = segment.upper()
    side    = side.upper()

    if segment == "EQUITY":
        turnover  = price * quantity                              # Rs

        brokerage = min(BROKERAGE_EQUITY_FLAT,
                        BROKERAGE_EQUITY_RATE * turnover)        # capped at flat

        stt       = STT_EQUITY_RATE * turnover                   # both sides
        exc_txn   = EXC_TXN_EQUITY_RATE * turnover
        sebi      = SEBI_TURNOVER_FEE_RATE * turnover
        stamp     = STAMP_DUTY_EQUITY_RATE * turnover if side == "BUY" else 0.0
        gst       = GST_RATE * (brokerage + exc_txn)             # on services only

        return round(brokerage + stt + exc_txn + sebi + stamp + gst, 2)

    elif segment == "OPTIONS":
        # Premium turnover = premium per share × lot size × number of lots
        turnover  = price * lot_size * quantity                   # Rs

        brokerage = BROKERAGE_OPTIONS_FLAT                        # flat, always
        stt       = STT_OPTIONS_SELL_RATE * turnover if side == "SELL" else 0.0
        exc_txn   = EXC_TXN_OPTIONS_RATE * turnover
        sebi      = SEBI_TURNOVER_FEE_RATE * turnover
        stamp     = STAMP_DUTY_OPTIONS_RATE * turnover if side == "BUY" else 0.0
        gst       = GST_RATE * (brokerage + exc_txn)

        return round(brokerage + stt + exc_txn + sebi + stamp + gst, 2)

    else:
        raise ValueError(
            f"Unknown segment {segment!r}. Use 'EQUITY' or 'OPTIONS'."
        )
