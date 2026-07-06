import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path

HEARTBEAT_FILE = Path("C:/trading_bot/heartbeat.json")

def record_heartbeat(script_name: str, status: str) -> None:
    """
    Appends a heartbeat event to heartbeat.json.
    status: 'START', 'FINISH', or 'ERROR'.
    Never raises exceptions.
    """
    try:
        now = datetime.now()
        entry = {
            "script": script_name,
            "status": status,
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d")
        }
        
        data = []
        if HEARTBEAT_FILE.exists():
            try:
                with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except:
                data = []
        
        data.append(entry)
        
        # Cleanup: Keep last 60 days
        cutoff = date.today() - timedelta(days=60)
        clean_data = []
        for d in data:
            try:
                if date.fromisoformat(d.get("date", "2000-01-01")) >= cutoff:
                    clean_data.append(d)
            except:
                continue
                
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)
    except:
        pass

def check_today(expected: dict) -> list:
    """
    Checks if expected scripts have run today.
    expected: {script_name: "HH:MM"} latest expected start time.
    Returns list of problem dicts.
    """
    problems = []
    try:
        data = []
        if HEARTBEAT_FILE.exists():
            with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        
        today_str = date.today().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M")
        
        for script, latest_start in expected.items():
            script_runs = [d for d in data if d.get("script") == script and d.get("date") == today_str]
            
            if script == "position_monitor":
                # Special: at least one START+FINISH/ERROR pair after 09:30
                relevant = [d for d in script_runs if d.get("timestamp", "").split("T")[1][:5] >= "09:30"]
                starts = [d for d in relevant if d["status"] == "START"]
                has_pair = False
                for s in starts:
                    if any(d for d in relevant if d["status"] in ("FINISH", "ERROR") and d["timestamp"] > s["timestamp"]):
                        has_pair = True
                        break
                if now_time >= latest_start and not has_pair:
                    problems.append({"script": script, "problem": "NO_START" if not starts else "NO_FINISH"})
                continue

            # Standard scripts
            starts = [d for d in script_runs if d["status"] == "START"]
            if not starts:
                if now_time >= latest_start:
                    problems.append({"script": script, "problem": "NO_START"})
            else:
                # Most recent start must have a corresponding end (FINISH or ERROR)
                last_start = starts[-1]
                has_end = any(d for d in script_runs if d["status"] in ("FINISH", "ERROR") and d["timestamp"] > last_start["timestamp"])
                if not has_end:
                    problems.append({"script": script, "problem": "NO_FINISH"})
    except:
        pass
    return problems
