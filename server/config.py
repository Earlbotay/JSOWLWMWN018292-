import os
import time

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "0"))
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/earlxz")

PAT_TOKEN = os.environ["PAT_TOKEN"]
PRIVATE_REPO = os.environ["PRIVATE_REPO"]   # owner/repo-private
VIDEO_URL = os.environ.get("VIDEO_URL", "")  # Direct link to video (catbox etc)

BOT_NAME = "E A R L  S T O R E  \u2014  B U I L D  A P K"
OWNER_USERNAME = "earlxz"
COUNTRY_FLAG = "\U0001f1f2\U0001f1fe"

SERVER_START_TIME = time.time()
MAX_RUNTIME_SECONDS = (5 * 60 * 60) - 600   # 4h50m

TEMP_DIR = "/tmp/earl_builds"
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Telegram Bot API Local Server ────────────────────────
API_ID = os.environ.get("API_ID", "")
API_HASH = os.environ.get("API_HASH", "")
USE_LOCAL_API = bool(API_ID and API_HASH)
LOCAL_API_URL = "http://localhost:8081"

# ── Web Server / Cloudflare Tunnel ───────────────────────
WEB_PORT = 8080
