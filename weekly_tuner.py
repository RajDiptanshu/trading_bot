"""
WEEKLY TUNER — Claude Opus 4.6
================================
Runs every Sunday at 6:00 PM via Task Scheduler.
Takes 5-10 minutes. Do not interrupt.

PURPOSE:
  Reviews every trade from the past week.
  Identifies what worked, what failed, and why.
  Rewrites strategy_memory.json with improved rules.
  This is the self-improvement engine of the system.

WHY OPUS (not Sonnet):
  Opus has deeper reasoning for pattern recognition.
  It reads 20-50 trades and finds non-obvious patterns.
  Example: "HINDALCO losses all occurred when ATR > 3.2%
           and RSI was above 58. Add combined filter."
  Sonnet would miss this. Opus finds it.

WHAT IT CAN CHANGE:
  - RSI entry range (if too wide, tighten it)
  - Minimum score threshold (raise if too many losses)
  - Stock blacklist (add persistent losers)
  - Stock favorites (add consistent winners)
  - Sector limits (adjust based on sector performance)
  - Kelly criterion (enable after 10 trades)
  - Circuit breaker sensitivity

WHAT IT CANNOT CHANGE:
  - Capital amount
  - VIX thresholds (structural market rules)
  - Deployment gate criteria (must remain objective)

Token budget: ~10,000 input + ~2,000 output = Rs 25.50/Sunday
"""

import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
import json
import math
import random
from datetime import datetime, date, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "strategy_memory.json"
TRADES_FILE = BASE_DIR / "paper_trades.json"
LOG_FILE    = BASE_DIR / "decision_log.json"
BACKUP_DIR  = BASE_DIR / "memory_backups"

BACKUP_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════
# FILE I/O
# ══════════════════════════════════════════════════════════════

def load_memory() -> dict:
    with open(MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_memory(memory: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)

def load_trades() -> list:
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE, encoding="utf-8") as f:
        return json.load(f)

def backup_memory(memory: dict):
    """Saves a timestamped backup before any changes."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_file = BACKUP_DIR / f"strategy_memory_{timestamp}.json"
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)
    print(f"  Backup saved: {backup_file.name}")
    return backup_file

# ══════════════════════════════════════════════════════════════
# TRADE ANALYZER — pure Python, no AI
# ══════════════════════════════════════════════════════════════

def get_this_weeks_trades(all_trades: list) -> list:
    """Returns trades closed or opened in the last 7 days."""
    cutoff = date.today() - timedelta(days=7)
    week_trades = []
    for t in all_trades:
        trade_date = date.fromisoformat(t.get("date", "2000-01-01"))
        exit_date  = t.get("exit_date")
        if trade_date >= cutoff:
            week_trades.append(t)
        elif exit_date and exit_date != "OPEN":
            try:
                if date.fromisoformat(exit_date) >= cutoff:
                    week_trades.append(t)
            except Exception:
                pass
    return week_trades


def calculate_weekly_stats(week_trades: list) -> dict:
    """
    Pure Python stats — no AI.
    Calculates all the numbers Opus will use for pattern analysis.
    """
    if not week_trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "open": 0,
            "win_rate_pct": 0,
            "total_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "expectancy": 0,
            "profit_factor": 0,
            "best_trade": None,
            "worst_trade": None,
            "by_strategy": {},
            "by_sector": {},
            "by_instrument": {},
            "by_score": {},
            "losing_stocks": [],
            "winning_stocks": [],
        }

    closed = [t for t in week_trades if t.get("status") == "CLOSED"]
    open_t = [t for t in week_trades if t.get("status") == "OPEN"]
    wins   = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]

    total_win_pnl  = sum(t.get("pnl", 0) for t in wins)
    total_loss_pnl = abs(sum(t.get("pnl", 0) for t in losses))

    # By strategy breakdown
    by_strategy = {}
    for t in closed:
        strat = t.get("strategy", "UNKNOWN")
        if strat not in by_strategy:
            by_strategy[strat] = {"trades": 0, "wins": 0, "pnl": 0}
        by_strategy[strat]["trades"] += 1
        if (t.get("pnl") or 0) > 0:
            by_strategy[strat]["wins"] += 1
        by_strategy[strat]["pnl"] = round(
            by_strategy[strat]["pnl"] + (t.get("pnl") or 0), 2
        )

    # By sector breakdown
    by_sector = {}
    for t in closed:
        sec = t.get("sector", "Other")
        if sec not in by_sector:
            by_sector[sec] = {"trades": 0, "wins": 0, "pnl": 0}
        by_sector[sec]["trades"] += 1
        if (t.get("pnl") or 0) > 0:
            by_sector[sec]["wins"] += 1
        by_sector[sec]["pnl"] = round(
            by_sector[sec]["pnl"] + (t.get("pnl") or 0), 2
        )

    # By instrument
    by_instrument = {}
    for t in closed:
        inst = t.get("instrument", "EQUITY")
        if inst not in by_instrument:
            by_instrument[inst] = {"trades": 0, "wins": 0, "pnl": 0}
        by_instrument[inst]["trades"] += 1
        if (t.get("pnl") or 0) > 0:
            by_instrument[inst]["wins"] += 1
        by_instrument[inst]["pnl"] = round(
            by_instrument[inst]["pnl"] + (t.get("pnl") or 0), 2
        )

    # By score
    by_score = {}
    for t in closed:
        score = str(t.get("score", "?"))
        if score not in by_score:
            by_score[score] = {"trades": 0, "wins": 0, "pnl": 0}
        by_score[score]["trades"] += 1
        if (t.get("pnl") or 0) > 0:
            by_score[score]["wins"] += 1
        by_score[score]["pnl"] = round(
            by_score[score]["pnl"] + (t.get("pnl") or 0), 2
        )

    # Best and worst trades
    best  = max(closed, key=lambda t: t.get("pnl") or 0) if closed else None
    worst = min(closed, key=lambda t: t.get("pnl") or 0) if closed else None

    # Losing and winning stocks
    stock_pnl = {}
    for t in closed:
        sym = t.get("symbol", "?")
        stock_pnl[sym] = stock_pnl.get(sym, 0) + (t.get("pnl") or 0)

    losing_stocks  = [s for s, p in stock_pnl.items() if p < 0]
    winning_stocks = [s for s, p in stock_pnl.items() if p > 0]

    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    avg_win  = round(total_win_pnl / len(wins), 0) if wins else 0
    avg_loss = round(total_loss_pnl / len(losses), 0) if losses else 0
    expectancy = round(
        (win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss), 0
    ) if closed else 0

    return {
        "total_trades":    len(closed),
        "wins":            len(wins),
        "losses":          len(losses),
        "open":            len(open_t),
        "win_rate_pct":    win_rate,
        "total_pnl":       round(sum(t.get("pnl", 0) for t in closed), 2),
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "expectancy":      expectancy,
        "profit_factor":   round(total_win_pnl / max(total_loss_pnl, 1), 2),
        "best_trade":      {
            "symbol": best["symbol"], "pnl": best["pnl"],
            "strategy": best.get("strategy"), "score": best.get("score")
        } if best else None,
        "worst_trade":     {
            "symbol": worst["symbol"], "pnl": worst["pnl"],
            "strategy": worst.get("strategy"), "score": worst.get("score")
        } if worst else None,
        "by_strategy":     by_strategy,
        "by_sector":       by_sector,
        "by_instrument":   by_instrument,
        "by_score":        by_score,
        "losing_stocks":   losing_stocks,
        "winning_stocks":  winning_stocks,
        "stock_pnl":       stock_pnl,
    }


# ══════════════════════════════════════════════════════════════
# DEPLOYMENT GATE CHECKER — pure Python
# ══════════════════════════════════════════════════════════════

def check_deployment_gate(memory: dict) -> dict:
    """
    Checks all deployment criteria.
    Returns gate status for Opus to include in its report.
    Pure math. No AI.
    """
    perf     = memory["performance_live"]
    criteria = memory["meta"]["deployment_criteria"]

    checks = {
        "weeks_paper_trading": {
            "required": criteria["min_weeks_paper_trading"],
            "current":  memory["meta"]["weeks_of_live_data"],
            "passed":   memory["meta"]["weeks_of_live_data"] >= criteria["min_weeks_paper_trading"],
        },
        "total_trades": {
            "required": criteria["min_total_trades"],
            "current":  perf.get("closed_trades", 0),
            "passed":   perf.get("closed_trades", 0) >= criteria["min_total_trades"],
        },
        "win_rate": {
            "required": criteria["min_win_rate_pct"],
            "current":  perf.get("win_rate_pct", 0),
            "passed":   perf.get("win_rate_pct", 0) >= criteria["min_win_rate_pct"],
        },
        "positive_expectancy": {
            "required": criteria["min_expectancy_per_trade"],
            "current":  perf.get("expectancy_per_trade_inr", 0),
            "passed":   perf.get("expectancy_per_trade_inr", 0) > 0,
        },
        "max_drawdown": {
            "required": criteria["max_drawdown_pct"],
            "current":  perf.get("max_drawdown_pct", 0),
            "passed":   perf.get("max_drawdown_pct", 0) <= criteria["max_drawdown_pct"],
        },
    }

    all_passed = all(c["passed"] for c in checks.values())

    return {
        "ready_for_deployment": all_passed,
        "checks": checks,
        "summary": f"{sum(1 for c in checks.values() if c['passed'])}/{len(checks)} criteria passed",
    }


# ══════════════════════════════════════════════════════════════
# STATISTICAL SIGNIFICANCE — pure Python
# ══════════════════════════════════════════════════════════════

def test_statistical_significance(all_trades: list) -> dict:
    closed = [t for t in all_trades if t.get('status') == 'CLOSED']
    total = len(closed)
    if total < 10:
        return {
            'status': 'INSUFFICIENT_DATA',
            'message': f'Only {total} closed trades — need at least 10 to test significance',
            'p_value': None,
            'significant': False,
        }
    wins = len([t for t in closed if (t.get('pnl') or 0) > 0])
    # One-sided binomial test: H0 = random 50% win rate, H1 = win rate > 50%
    # Using normal approximation (valid for n >= 10)
    p_hat = wins / total
    se = math.sqrt(0.5 * 0.5 / total)
    z = (p_hat - 0.5) / se
    # One-sided p-value from z-score using approximation
    # p = 1 - CDF(z) approximated via erfc
    p_value = 0.5 * math.erfc(z / math.sqrt(2))
    significant = p_value < 0.05
    return {
        'status': 'TESTED',
        'total_closed': total,
        'wins': wins,
        'win_rate_pct': round(p_hat * 100, 1),
        'z_score': round(z, 3),
        'p_value': round(p_value, 4),
        'significant': significant,
        'verdict': 'REAL EDGE (p<0.05)' if significant else 'INSUFFICIENT EVIDENCE — may be random',
        'note': 'Null hypothesis: 50% win rate (random). p<0.05 means edge is statistically real.',
    }


# ══════════════════════════════════════════════════════════════
# MONTE CARLO SIMULATION — pure Python
# ══════════════════════════════════════════════════════════════

def run_monte_carlo(all_trades: list, simulations: int = 10000) -> dict:
    closed = [t for t in all_trades if t.get('status') == 'CLOSED']
    pnl_list = [t.get('pnl', 0) for t in closed if t.get('pnl') is not None]
    if len(pnl_list) < 10:
        return {
            'status': 'INSUFFICIENT_DATA',
            'message': f'Only {len(pnl_list)} closed trades with P&L — need at least 10',
        }
    max_drawdowns = []
    final_pnls = []
    for _ in range(simulations):
        shuffled = random.sample(pnl_list, len(pnl_list))
        peak = cumulative = 0.0
        worst_dd = 0.0
        for p in shuffled:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = cumulative - peak
            if dd < worst_dd:
                worst_dd = dd
        max_drawdowns.append(worst_dd)
        final_pnls.append(cumulative)
    max_drawdowns.sort()
    final_pnls.sort()
    p5_dd  = max_drawdowns[int(0.05 * simulations)]
    p50_dd = max_drawdowns[int(0.50 * simulations)]
    p95_dd = max_drawdowns[int(0.95 * simulations)]
    p5_pnl = final_pnls[int(0.05 * simulations)]
    p50_pnl = final_pnls[int(0.50 * simulations)]
    ruin_threshold = -50000  # Rs50,000 drawdown = ruin for Rs5L capital
    ruin_pct = round(len([d for d in max_drawdowns if d <= ruin_threshold]) / simulations * 100, 1)
    return {
        'status': 'COMPLETE',
        'simulations': simulations,
        'trades_used': len(pnl_list),
        'drawdown_p5_inr': round(p5_dd, 0),
        'drawdown_median_inr': round(p50_dd, 0),
        'drawdown_p95_inr': round(p95_dd, 0),
        'final_pnl_p5_inr': round(p5_pnl, 0),
        'final_pnl_median_inr': round(p50_pnl, 0),
        'ruin_probability_pct': ruin_pct,
        'note': 'p5 drawdown = worst 5% of luck scenarios. Ruin = drawdown > Rs50,000.',
    }


# ══════════════════════════════════════════════════════════════
# OPUS TUNER — the one AI call of the week
# ══════════════════════════════════════════════════════════════

def run_opus_tuner(
    week_trades: list,
    stats: dict,
    memory: dict,
    all_trades: list,
    deployment_gate: dict,
    significance: dict,
    monte_carlo: dict
) -> dict:
    """
    THE WEEKLY AI CALL — Claude Opus 4.6.

    Opus receives:
      - This week's trades with full details
      - Calculated statistics (pure Python math)
      - Current strategy_memory.json rules
      - All-time performance summary
      - Deployment gate status
      - Statistical significance data
      - Monte Carlo simulation data

    Opus outputs:
      - Updated sections of strategy_memory.json
      - Reasoning for each change
      - Changelog entry for this week
      - Deployment recommendation

    Target: ~10,000 tokens in, ~2,000 out = Rs 25.50
    """
    print("  Calling Claude Opus 4.6 for weekly analysis...")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Build compact all-time summary
    all_closed = [t for t in all_trades if t.get("status") == "CLOSED"]
    alltime_summary = {
        "total_closed":  len(all_closed),
        "total_pnl":     round(sum(t.get("pnl", 0) for t in all_closed), 2),
        "overall_return_pct": memory["performance_live"].get("total_return_pct", 0),
        "month_progress_pct": memory["performance_live"].get("month_progress_pct", 0),
    }

    # Current rules — what Opus is allowed to change
    current_rules = {
        "entry_rules": {
            "minimum_score_to_trade": memory["entry_rules"]["minimum_score_to_trade"],
            "rsi_long_min":           memory["entry_rules"]["rsi_long_min"],
            "rsi_long_max":           memory["entry_rules"]["rsi_long_max"],
            "rsi_mean_reversion_trigger": memory["entry_rules"]["rsi_mean_reversion_trigger"],
            "ma20_must_be_rising":    memory["entry_rules"]["ma20_must_be_rising"],
            "strategies_enabled":     memory["entry_rules"]["strategies_enabled"],
        },
        "exit_rules": {
            "target_atr_multiple":   memory["exit_rules"]["target_atr_multiple"],
            "stoploss_atr_multiple": memory["exit_rules"]["stoploss_atr_multiple"],
            "time_stop_days":        memory["exit_rules"]["time_stop_days"],
        },
        "blacklist": memory["stock_universe"]["blacklist"]["stocks"],
        "favorites": memory["stock_universe"]["high_conviction_favorites"]["stocks"],
        "kelly_enabled": memory["capital_rules"]["kelly_params"]["enabled"],
        "circuit_consecutive_loss_limit": memory["circuit_breakers"]["consecutive_loss_limit"],
    }

    # Compact week trade details for Opus
    trade_details = [
        {
            "sym":       t.get("symbol"),
            "direction": t.get("direction"),
            "strategy":  t.get("strategy"),
            "instrument": t.get("instrument"),
            "score":     t.get("score"),
            "sector":    t.get("sector"),
            "entry":     t.get("entry_price"),
            "exit":      t.get("exit_price"),
            "pnl":       t.get("pnl"),
            "result":    t.get("result"),
            "exit_reason": t.get("exit_reason"),
            "days_held": (
                (date.fromisoformat(t["exit_date"]) - date.fromisoformat(t["date"])).days
                if t.get("exit_date") and t["exit_date"] != "OPEN"
                else None
            ),
            "rsi_at_entry":  t.get("score"),
            "news_sentiment": t.get("news_sentiment"),
        }
        for t in week_trades
        if t.get("status") == "CLOSED"
    ]

    system_prompt = """You are a quantitative trading strategist reviewing weekly paper trading performance.
Your job: analyze the trades, identify patterns, and output ONLY a JSON object with specific rule updates.
Be conservative — only change rules that have clear evidence from the data.
Do not change what is working. Fix only what is consistently failing.
Output ONLY valid JSON. No explanations outside the JSON."""

    user_prompt = f"""WEEKLY PERFORMANCE REPORT — Week ending {date.today().isoformat()}

THIS WEEK'S TRADES:
{json.dumps(trade_details, indent=2)}

WEEKLY STATISTICS:
{json.dumps(stats, indent=2)}

ALL-TIME SUMMARY:
{json.dumps(alltime_summary, indent=2)}

CURRENT RULES (what you can modify):
{json.dumps(current_rules, indent=2)}

DEPLOYMENT GATE STATUS:
{json.dumps(deployment_gate, indent=2)}

STATISTICAL SIGNIFICANCE: {json.dumps(significance, indent=2)}

MONTE CARLO SIMULATION: {json.dumps(monte_carlo, indent=2)}

TARGET: 20% monthly return (Rs 1,00,000 on Rs 5,00,000 capital)

OUTPUT a JSON object with this EXACT structure:
{{
  "rule_changes": {{
    "minimum_score_to_trade": 6,
    "rsi_long_min": 40,
    "rsi_long_max": 63,
    "rsi_mean_reversion_trigger": 30,
    "target_atr_multiple": 3.0,
    "stoploss_atr_multiple": 2.0,
    "time_stop_days": 12,
    "strategies_enabled": {{
      "momentum_long": true,
      "mean_reversion_long": true,
      "short_momentum": true
    }},
    "circuit_consecutive_loss_limit": 3,
    "kelly_enabled": false
  }},
  "blacklist_add": [],
  "blacklist_remove": [],
  "favorites_add": [],
  "favorites_remove": [],
  "insights": [
    "One specific insight from this week's data",
    "Another specific insight with stock names and numbers"
  ],
  "next_week_focus": [
    "Specific action item for next week"
  ],
  "deployment_recommendation": "NOT_READY or READY or ALMOST_READY",
  "deployment_notes": "Specific reason based on the gate checks",
  "changelog_summary": "One paragraph summary of what changed and why"
}}

RULES FOR CHANGES:
- Only add to blacklist if a stock lost money 3+ times. Name the stock and the pattern.
- Only tighten RSI if losses consistently happened above a specific RSI level.
- Only raise minimum_score if trades at current threshold are mostly losing.
- If win_rate > 55% — do NOT change entry rules. They are working.
- If total_trades < 5 — return ALL current values unchanged. Not enough data.
- Kelly should only be enabled if closed_trades >= 10 AND win_rate >= 45%.
- Be specific in insights — name stocks, name RSI levels, name strategies.
- Return ONLY the JSON object. Nothing else."""

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)

        usage    = response.usage
        cost_usd = (usage.input_tokens * 15 + usage.output_tokens * 75) / 1_000_000
        cost_inr = cost_usd * 85
        print(f"  Tokens: {usage.input_tokens} in / {usage.output_tokens} out"
              f" = Rs {cost_inr:.2f}")

        return result

    except json.JSONDecodeError as e:
        print(f"  WARNING: Opus JSON parse error: {e}")
        print(f"  Returning no changes this week")
        return None
    except Exception as e:
        print(f"  WARNING: Opus API error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# MEMORY UPDATER — applies Opus changes safely
# ══════════════════════════════════════════════════════════════

def apply_changes(memory: dict, opus_output: dict, stats: dict, week_num: int) -> dict:
    """
    Applies Opus recommendations to strategy_memory.json.
    Validates each change before applying.
    Records everything in the changelog.
    Pure Python safety layer around Opus output.
    """
    if not opus_output:
        print("  No changes to apply this week.")
        return memory

    changes_made = []
    rule_changes = opus_output.get("rule_changes", {})

    # ── Entry rules ──────────────────────────────────────────
    entry = memory["entry_rules"]

    if "minimum_score_to_trade" in rule_changes:
        new_val = int(rule_changes["minimum_score_to_trade"])
        if 5 <= new_val <= 9:  # sanity bounds
            if new_val != entry["minimum_score_to_trade"]:
                changes_made.append(
                    f"min_score: {entry['minimum_score_to_trade']} -> {new_val}"
                )
                entry["minimum_score_to_trade"] = new_val

    if "rsi_long_min" in rule_changes:
        new_val = int(rule_changes["rsi_long_min"])
        if 30 <= new_val <= 50:
            if new_val != entry["rsi_long_min"]:
                changes_made.append(f"rsi_long_min: {entry['rsi_long_min']} -> {new_val}")
                entry["rsi_long_min"] = new_val

    if "rsi_long_max" in rule_changes:
        new_val = int(rule_changes["rsi_long_max"])
        if 55 <= new_val <= 70:
            if new_val != entry["rsi_long_max"]:
                changes_made.append(f"rsi_long_max: {entry['rsi_long_max']} -> {new_val}")
                entry["rsi_long_max"] = new_val

    if "rsi_mean_reversion_trigger" in rule_changes:
        new_val = int(rule_changes["rsi_mean_reversion_trigger"])
        if 20 <= new_val <= 35:
            if new_val != entry["rsi_mean_reversion_trigger"]:
                changes_made.append(
                    f"rsi_mr_trigger: {entry['rsi_mean_reversion_trigger']} -> {new_val}"
                )
                entry["rsi_mean_reversion_trigger"] = new_val

    if "strategies_enabled" in rule_changes:
        new_strats = rule_changes["strategies_enabled"]
        for strat, enabled in new_strats.items():
            if strat in entry["strategies_enabled"]:
                if enabled != entry["strategies_enabled"][strat]:
                    changes_made.append(
                        f"strategy_{strat}: {entry['strategies_enabled'][strat]} -> {enabled}"
                    )
                    entry["strategies_enabled"][strat] = enabled

    memory["entry_rules"] = entry

    # ── Exit rules ────────────────────────────────────────────
    exit_r = memory["exit_rules"]

    if "target_atr_multiple" in rule_changes:
        new_val = float(rule_changes["target_atr_multiple"])
        if 1.5 <= new_val <= 5.0:
            if new_val != exit_r["target_atr_multiple"]:
                changes_made.append(
                    f"target_atr: {exit_r['target_atr_multiple']} -> {new_val}"
                )
                exit_r["target_atr_multiple"] = new_val

    if "stoploss_atr_multiple" in rule_changes:
        new_val = float(rule_changes["stoploss_atr_multiple"])
        if 1.0 <= new_val <= 3.0:
            if new_val != exit_r["stoploss_atr_multiple"]:
                changes_made.append(
                    f"sl_atr: {exit_r['stoploss_atr_multiple']} -> {new_val}"
                )
                exit_r["stoploss_atr_multiple"] = new_val

    if "time_stop_days" in rule_changes:
        new_val = int(rule_changes["time_stop_days"])
        if 5 <= new_val <= 20:
            if new_val != exit_r["time_stop_days"]:
                changes_made.append(f"time_stop: {exit_r['time_stop_days']} -> {new_val}")
                exit_r["time_stop_days"] = new_val

    memory["exit_rules"] = exit_r

    # ── Blacklist ─────────────────────────────────────────────
    current_blacklist = memory["stock_universe"]["blacklist"]["stocks"]

    for sym in opus_output.get("blacklist_add", []):
        if sym not in current_blacklist:
            current_blacklist.append(sym)
            changes_made.append(f"BLACKLISTED: {sym}")

    for sym in opus_output.get("blacklist_remove", []):
        if sym in current_blacklist:
            current_blacklist.remove(sym)
            changes_made.append(f"UN-BLACKLISTED: {sym}")

    memory["stock_universe"]["blacklist"]["stocks"] = current_blacklist

    # ── Favorites ─────────────────────────────────────────────
    current_favorites = memory["stock_universe"]["high_conviction_favorites"]["stocks"]

    for sym in opus_output.get("favorites_add", []):
        if sym not in current_favorites:
            current_favorites.append(sym)
            changes_made.append(f"FAVORITED: {sym}")

    for sym in opus_output.get("favorites_remove", []):
        if sym in current_favorites:
            current_favorites.remove(sym)
            changes_made.append(f"UN-FAVORITED: {sym}")

    memory["stock_universe"]["high_conviction_favorites"]["stocks"] = current_favorites

    # ── Circuit breaker ───────────────────────────────────────
    if "circuit_consecutive_loss_limit" in rule_changes:
        new_val = int(rule_changes["circuit_consecutive_loss_limit"])
        if 2 <= new_val <= 6:
            old_val = memory["circuit_breakers"]["consecutive_loss_limit"]
            if new_val != old_val:
                changes_made.append(f"circuit_loss_limit: {old_val} -> {new_val}")
                memory["circuit_breakers"]["consecutive_loss_limit"] = new_val

    # ── Kelly criterion ───────────────────────────────────────
    kelly = memory["capital_rules"]["kelly_params"]
    if rule_changes.get("kelly_enabled") and not kelly["enabled"]:
        perf = memory["performance_live"]
        if perf.get("closed_trades", 0) >= 10 and perf.get("win_rate_pct", 0) >= 45:
            kelly["enabled"] = True
            kelly["current_win_rate"] = perf["win_rate_pct"] / 100
            kelly["avg_win_inr"]  = perf.get("avg_win_inr", 0)
            kelly["avg_loss_inr"] = perf.get("avg_loss_inr", 0)
            wr    = kelly["current_win_rate"]
            ratio = kelly["avg_win_inr"] / max(kelly["avg_loss_inr"], 1)
            kelly["kelly_fraction"]      = round(max(wr - (1-wr)/ratio, 0), 4)
            kelly["half_kelly_fraction"] = round(kelly["kelly_fraction"] / 2, 4)
            changes_made.append(
                f"Kelly ENABLED: win_rate={wr:.1%} half_kelly={kelly['half_kelly_fraction']:.1%}"
            )
    memory["capital_rules"]["kelly_params"] = kelly

    # ── Update meta ───────────────────────────────────────────
    memory["meta"]["last_updated"] = date.today().isoformat()
    memory["meta"]["last_tuned_by"] = "weekly_tuner_opus"
    memory["meta"]["weeks_of_live_data"] = week_num

    # ── Deployment gate ───────────────────────────────────────
    deploy_rec = opus_output.get("deployment_recommendation", "NOT_READY")
    if deploy_rec == "READY":
        memory["meta"]["deployment_ready"] = True

    # ── Changelog entry ───────────────────────────────────────
    changelog_entry = {
        "date":    date.today().isoformat(),
        "week":    week_num,
        "changed_by": "weekly_tuner_opus",
        "changes": changes_made,
        "performance_this_week": {
            "trades":      stats["total_trades"],
            "wins":        stats["wins"],
            "losses":      stats["losses"],
            "pnl_inr":     stats["total_pnl"],
            "win_rate_pct": stats["win_rate_pct"],
        },
        "insights":        opus_output.get("insights", []),
        "next_week_focus": opus_output.get("next_week_focus", []),
        "deployment_recommendation": deploy_rec,
        "deployment_notes": opus_output.get("deployment_notes", ""),
        "changelog_summary": opus_output.get("changelog_summary", ""),
    }
    memory["weekly_changelog"].append(changelog_entry)

    return memory, changes_made


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    start_time = datetime.now()
    today      = date.today().isoformat()
    week_num   = len(json.load(open(MEMORY_FILE, encoding="utf-8"))
                     .get("weekly_changelog", [])) + 1

    print(f"\n{'='*60}")
    print(f"  WEEKLY TUNER - {today} (Week {week_num})")
    print(f"  Claude Opus 4.6 - Self-improvement engine")
    print(f"{'='*60}\n")

    # Load data
    memory     = load_memory()
    all_trades = load_trades()

    # Backup before any changes
    print("  Backing up strategy_memory.json...")
    backup_memory(memory)

    # Get this week's trades
    week_trades = get_this_weeks_trades(all_trades)
    print(f"  This week's trades: {len(week_trades)}")

    if not week_trades:
        print("  No trades this week. Updating week counter and exiting.")
        memory["meta"]["weeks_of_live_data"] = week_num
        memory["meta"]["last_updated"]       = today
        changelog_entry = {
            "date":    today,
            "week":    week_num,
            "changed_by": "weekly_tuner",
            "changes": [],
            "performance_this_week": {
                "trades": 0, "wins": 0, "losses": 0,
                "pnl_inr": 0, "win_rate_pct": 0,
            },
            "insights": ["No trades this week — system may not have found qualifying signals."],
            "next_week_focus": ["Continue paper trading. Monitor if score thresholds are too strict."],
            "deployment_recommendation": "NOT_READY",
            "deployment_notes": "Need minimum 20 trades to evaluate.",
            "changelog_summary": f"Week {week_num}: No closed trades. No rule changes.",
        }
        memory["weekly_changelog"].append(changelog_entry)
        save_memory(memory)
        print(f"  Week {week_num} recorded. No changes made.")
        return

    # Calculate stats (pure Python)
    print("  Calculating weekly statistics...")
    stats = calculate_weekly_stats(week_trades)

    print(f"\n  WEEK {week_num} SUMMARY:")
    print(f"  Trades : {stats['total_trades']} closed, {stats['open']} open")
    print(f"  Win rate: {stats['win_rate_pct']}%")
    print(f"  P&L     : Rs{stats['total_pnl']:+,.0f}")
    print(f"  Best    : {stats['best_trade']}")
    print(f"  Worst   : {stats['worst_trade']}")

    if stats["by_strategy"]:
        print(f"\n  By strategy:")
        for strat, s in stats["by_strategy"].items():
            wr = round(s["wins"]/max(s["trades"],1)*100, 0)
            print(f"    {strat:<20} {s['trades']} trades  {wr:.0f}% WR  Rs{s['pnl']:+,.0f}")

    # Check deployment gate
    print(f"\n  Checking deployment gate...")
    deployment_gate = check_deployment_gate(memory)
    print(f"  Gate: {deployment_gate['summary']}")

    print(f'  Running significance test...')
    significance = test_statistical_significance(all_trades)
    if significance['status'] == 'INSUFFICIENT_DATA':
        print(f'  Significance: {significance["message"]}')
    else:
        print(f'  Significance: {significance["verdict"]} (p={significance["p_value"]}, z={significance["z_score"]})')

    print(f'  Running Monte Carlo ({10000} simulations)...')
    monte_carlo = run_monte_carlo(all_trades)
    if monte_carlo['status'] == 'INSUFFICIENT_DATA':
        print(f'  Monte Carlo: {monte_carlo["message"]}')
    else:
        print(f'  Monte Carlo drawdown p5/median/p95: Rs{monte_carlo["drawdown_p5_inr"]:,.0f} / Rs{monte_carlo["drawdown_median_inr"]:,.0f} / Rs{monte_carlo["drawdown_p95_inr"]:,.0f}')
        print(f'  Ruin probability (>Rs50K drawdown): {monte_carlo["ruin_probability_pct"]}%')

    # Run Opus tuner
    print(f"\n  Running Opus analysis (~10 seconds)...")
    opus_output = run_opus_tuner(
        week_trades, stats, memory, all_trades, deployment_gate, significance, monte_carlo
    )

    if not opus_output:
        print("  Opus analysis failed. No changes applied.")
        print("  Backup available in memory_backups/")
        return

    # Apply changes
    print(f"\n  Applying rule changes...")
    memory, changes_made = apply_changes(memory, opus_output, stats, week_num)

    # Save updated memory
    save_memory(memory)

    # Print what changed
    print(f"\n{'='*60}")
    print(f"  WEEKLY TUNER COMPLETE")
    print(f"{'='*60}")
    print(f"  Week number    : {week_num}")
    print(f"  Changes made   : {len(changes_made)}")
    for change in changes_made:
        print(f"    -> {change}")

    print(f"\n  OPUS INSIGHTS:")
    for insight in opus_output.get("insights", []):
        print(f"    * {insight}")

    print(f"\n  NEXT WEEK FOCUS:")
    for focus in opus_output.get("next_week_focus", []):
        print(f"    -> {focus}")

    deploy = opus_output.get("deployment_recommendation", "NOT_READY")
    deploy_icon = "READY" if deploy == "READY" else "NOT READY"
    print(f"\n  DEPLOYMENT STATUS: {deploy_icon}")
    print(f"  {opus_output.get('deployment_notes', '')}")

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n  Time taken: {elapsed}s")
    print(f"  Backup saved in: memory_backups/")
    print(f"  Updated: strategy_memory.json")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    from heartbeat import record_heartbeat
    record_heartbeat("weekly_tuner", "START")
    try:
        main()
        record_heartbeat("weekly_tuner", "FINISH")
    except Exception:
        record_heartbeat("weekly_tuner", "ERROR")
        raise