"""
agent4_watchdog.py — did today's scheduled runs actually happen?

Runs Mon-Fri at 16:05 (Task Scheduler). Reads agent4_heartbeat.json (written by
every entry/monitor run) and alerts via notify.py when a run is missing, when a
kill switch is active, or when the entry cycle saw only errors. A silent failure
of the 12:30 monitor on a crash day is exactly the scenario this exists for.
"""
from __future__ import annotations
import json, sys
from datetime import date, datetime
from pathlib import Path

if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from notify import notify, TRADING_BOT_DIR

HEARTBEAT = TRADING_BOT_DIR / "agent4_heartbeat.json"
EXPECTED = {"entry": "09:40", "monitor_midday": "12:45", "monitor_close": "15:55"}

def main():
    today = str(date.today())
    if date.today().weekday() >= 5:
        print("weekend — nothing expected")
        return
    hb = {}
    if HEARTBEAT.exists():
        try:
            hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            pass
    now_hm = datetime.now().strftime("%H:%M")
    problems = []
    # entry ran today?
    e = hb.get("entry", {})
    if e.get("date") != today and now_hm >= EXPECTED["entry"]:
        problems.append("entry cycle has NOT run today")
    # at least 2 monitor runs by close?
    m = hb.get("monitor", {})
    runs_today = m.get("runs_today", 0) if m.get("date") == today else 0
    if now_hm >= EXPECTED["monitor_close"] and runs_today < 2:
        problems.append(f"only {runs_today} monitor run(s) today (expected 2)")
    # kill switch / errors surfaced by the last runs
    if e.get("date") == today and e.get("halted"):
        problems.append(f"kill switch ACTIVE: {e.get('halt_reason')}")
    if m.get("date") == today and m.get("halted"):
        problems.append(f"kill switch ACTIVE: {m.get('halt_reason')}")
    if problems:
        notify("WATCHDOG " + " | ".join(problems) +
               " — check C:\\trading_bot\\logs\\ and Task Scheduler", "ALERT")
        print("ALERT:", problems)
    else:
        print(f"watchdog OK — entry {e.get('date')}, monitor runs today: {runs_today}")

if __name__ == "__main__":
    main()
