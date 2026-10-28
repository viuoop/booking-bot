import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан в .env")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DEMO_MODE = os.getenv("DEMO_MODE", "").strip() == "1"

TZ = ZoneInfo("Europe/Kyiv")
SLOT_MINUTES = 30
BOOKING_DAYS = 14
MIN_LEAD_MINUTES = 60
DAILY_SCHEDULE_HOUR = 20
REMINDER_HOURS = (24, 2)
DURATIONS = (30, 60, 90, 120)
DB_PATH = "booking.db"
