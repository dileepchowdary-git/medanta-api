"""All settings. Secrets come from .env (gitignored), never from code."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# The one client this service may ever serve. Fixed in code on purpose: no
# request parameter can make it return another client's data.
CLIENT_ID = 4161                                   # Jay Prabha Medanta, Patna

# Access control
API_KEY = os.getenv("MEDANTA_API_KEY", "")         # the key the Medanta robot sends
ALLOWED_IPS = {ip.strip() for ip in os.getenv("MEDANTA_ALLOWED_IPS", "").split(",") if ip.strip()}

# 5C read replica (same credentials the Yashoda service uses)
PG_HOST = os.getenv("PG_HOST_PROD", "")
PG_USER = os.getenv("PG_USER_PROD", "")
PG_PASSWORD = os.getenv("PG_PASSWORD_PROD", "")
PG_PORT = int(os.getenv("PG_PORT_PROD", "5432"))
PG_DB_STUDY = os.getenv("PG_DB_STUDY", "study")

# 5C API (key stays on this server)
FIVEC_BASE = os.getenv("FIVEC_BASE", "https://api.5cnetwork.com")
FIVEC_AUTH = os.getenv("FIVEC_AUTH", "")

# What is served
# "ALL" (default) = every modality; or a list like CT,MRI
MODALITIES = [m.strip().upper() for m in os.getenv("MEDANTA_MODALITIES", "ALL").split(",") if m.strip()]
ALL_MODALITIES = not MODALITIES or "ALL" in MODALITIES
DEFAULT_DAYS = int(os.getenv("MEDANTA_DEFAULT_DAYS", "2"))
MAX_DAYS = int(os.getenv("MEDANTA_MAX_DAYS", "7"))
REPORT_CACHE_SECS = int(os.getenv("MEDANTA_REPORT_CACHE_SECS", "600"))

# Failure alerts (optional)
GCHAT_WEBHOOK = os.getenv("MEDANTA_GCHAT_WEBHOOK", "")
# A GChat message for every pasted report (0 = only failures, robot-down and the daily summary)
GCHAT_SUCCESS = os.getenv("MEDANTA_GCHAT_SUCCESS", "1") not in ("0", "false", "False", "")
# Robot-down alert: no /reports call from the robot for this long -> one GChat alert (0 = off)
ROBOT_DOWN_MIN = int(os.getenv("MEDANTA_ROBOT_DOWN_MIN", "30"))
# Daily GChat summary at this local hour (HH:MM, empty = off): pasted / failed / not pasted + why
SUMMARY_AT = os.getenv("MEDANTA_SUMMARY_AT", "21:00").strip()

# Runtime
HOST = os.getenv("MEDANTA_API_HOST", "127.0.0.1")   # local only; nginx is the public door
PORT = int(os.getenv("MEDANTA_API_PORT", "8003"))
DATA_DIR = BASE_DIR / "data"                        # status.sqlite3
LOG_DIR = BASE_DIR / "logs"
STATUS_DB = DATA_DIR / "status.sqlite3"
HTTP_TIMEOUT = int(os.getenv("MEDANTA_HTTP_TIMEOUT", "60"))
FIVEC_WORKERS = int(os.getenv("MEDANTA_FIVEC_WORKERS", "10"))   # parallel 5C calls for /reports


def check():
    """Refuse to start with missing secrets rather than run half-configured."""
    missing = [n for n, v in (("MEDANTA_API_KEY", API_KEY), ("PG_HOST_PROD", PG_HOST),
                              ("PG_USER_PROD", PG_USER), ("PG_PASSWORD_PROD", PG_PASSWORD),
                              ("FIVEC_AUTH", FIVEC_AUTH)) if not v]
    if missing:
        raise SystemExit(f"missing settings in .env: {', '.join(missing)}")
    if len(API_KEY) < 32:
        raise SystemExit("MEDANTA_API_KEY must be at least 32 characters")
