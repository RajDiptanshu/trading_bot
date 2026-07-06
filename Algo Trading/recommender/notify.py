"""
notify.py — one-line alerting for the trading system.

Every alert is ALWAYS appended to C:\\trading_bot\\logs\\alerts.log.
If TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID exist in C:\\trading_bot\\.env, the alert
is also pushed to Telegram (free: create a bot via @BotFather, message it once,
get your chat id from https://api.telegram.org/bot<TOKEN>/getUpdates).
Without Telegram config the system still works — alerts just stay in the log
and surface as a banner in the web UI.
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path

BASE_DIR        = Path(__file__).resolve().parent
TRADING_BOT_DIR = Path(os.getenv("TRADING_BOT_DIR", BASE_DIR.parents[1]))
ALERTS_LOG      = TRADING_BOT_DIR / "logs" / "alerts.log"
try:
    from dotenv import load_dotenv
    load_dotenv(TRADING_BOT_DIR / ".env", override=False)
except Exception:
    pass

def notify(msg: str, level: str = "WARN") -> dict:
    """Log always; Telegram when configured. Never raises."""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}"
    sent = {"logged": False, "telegram": False}
    try:
        ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        sent["logged"] = True
    except Exception:
        pass
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            import requests
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": f"[{level}] {msg}"}, timeout=10)
            sent["telegram"] = bool(r.ok)
        except Exception:
            pass
    return sent

if __name__ == "__main__":
    print(notify("notify.py test message", "INFO"))
