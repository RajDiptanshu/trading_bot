# 10 — Intervention Rules (the contract with myself)

*The kill switches protect the book. This document protects the experiment from me.
Signed before the first paper session, 2026-06-12. Edit only OUTSIDE market hours,
and never on a losing day.*

## I will intervene ONLY when one of these is true
1. **Code bug or data outage** — a run crashed, the watchdog alerted, fills are based on
   visibly wrong prices, or positions show NO_DATA. (Fix the system, not the trade.)
2. **A kill switch fired** — I review what happened. MAX_DRAWDOWN reset requires writing
   three sentences in this file: what happened, why, what changes (if anything).
3. **A structural event outside the system's design** — exchange halt, broker outage,
   regulatory change affecting F&O contracts (e.g. lot-size circular), war/black-swan gap.
   Action allowed: flatten paper book and pause the schedule — not "adjust" trades.

## I will NOT
- Close a position early because it "feels" wrong. The exit rules exist; the data decides.
- Skip an entry the system chose, or add one it didn't. Every override destroys the
  validity of the 6-month test — an overridden experiment proves nothing either way.
- Change ANY constant (risk %, stops, score thresholds, conviction floors) before 30
  closed trades, and then only with a written reason and a fresh line in the changelog below.
- Increase risk after a winning streak, or "double to recover" after a losing one.
- Start real money before the Month-6 gates in Notes\09 pass — no exceptions for
  "it's obviously working".

## Weekly review ritual (Sundays, after ML retrain)
Read the week's closed trades and decisions log. Ask only: did the SYSTEM follow its
rules? (Not: did it make money this week — single weeks are noise.)

## Changelog of interventions and parameter changes
| Date | What | Why | Outcome |
|---|---|---|---|
| | | | |
