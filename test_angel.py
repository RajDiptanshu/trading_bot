import os
import pyotp
from SmartApi import SmartConnect
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

API_KEY   = os.getenv("ANGEL_API_KEY")
CLIENT_ID = os.getenv("ANGEL_CLIENT_ID")
PASSWORD  = os.getenv("ANGEL_PASSWORD")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

try:
    obj  = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

    if data["status"]:
        print("Angel One connected successfully")
        print(f"User: {data['data']['name']}")
        print(f"Token received: YES")
    else:
        print(f"Connection failed: {data['message']}")

except Exception as e:
    print(f"Error: {e}")