import os
from dotenv import load_dotenv
load_dotenv()
g = os.getenv
API_KEY = g("ANGEL_API_KEY"); CLIENT = g("ANGEL_CLIENT_CODE")
PIN = g("ANGEL_PIN"); TOTP_SECRET = g("ANGEL_TOTP_SECRET")
SYMBOL = g("SYMBOL", "NIFTY")
N = int(g("STRIKES_EACH_SIDE", 5))
POLL_SEC = int(g("POLL_SEC", 5))
LOOKBACK_SEC = int(g("LOOKBACK_SEC", 300))
SL_PCT = float(g("SL_PCT", 0.20))
RR = float(g("RR", 1.5))
THRESH = float(g("SIGNAL_THRESHOLD", 0.35))
AI_MIN_CONF = float(g("AI_MIN_CONF", 0.55))
API_TOKEN = g("API_TOKEN", "change-me")
NSE_POLL_SEC = int(g("NSE_POLL_SEC", 60))
