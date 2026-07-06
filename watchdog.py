import sys
if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr: sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
import heartbeat

# Load env for Telegram credentials
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LOG_DIR = Path("C:/trading_bot/logs")
DECISION_LOG = Path("C:/trading_bot/decision_log.json")

def send_alert(msg: str):
    """Sends message to Telegram or fallback to log file."""
    if TOKEN and CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            resp = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
            if not resp.ok:
                raise Exception(f"Telegram API error: {resp.text}")
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")
            _log_to_file(msg)
    else:
        _log_to_file(msg)

def _log_to_file(msg: str):
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "watchdog_alerts.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} - {msg}\n")
    print(f"ALERT: {msg}")

def log_to_decision(problems: list):
    """Appends missed runs to decision_log.json."""
    if not DECISION_LOG.exists():
        return
    try:
        with open(DECISION_LOG, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        data.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "type": "MISSED_RUN",
            "details": problems
        })
        
        with open(DECISION_LOG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Failed to update decision_log.json: {e}")

def main():
    # Only run Monday to Friday
    if datetime.now().weekday() >= 5:
        return

    # script_name -> latest expected start time (HH:MM)
    expected = {
        "morning_scan": "09:00",
        "morning_crew": "09:10",
        "execution_engine": "09:20",
        "midday_sentinel": "11:05",
        "position_monitor": "15:20"
    }
    
    problems = heartbeat.check_today(expected)
    if problems:
        msg_lines = ["🚨 TRADING BOT ALERT - MISSED/CRASHED RUNS:"]
        for p in problems:
            msg_lines.append(f"- {p['script']}: {p['problem']}")
        
        full_msg = "\n".join(msg_lines)
        send_alert(full_msg)
        log_to_decision(problems)
    else:
        print("Watchdog: All systems checked and healthy.")

if __name__ == "__main__":
    main()
