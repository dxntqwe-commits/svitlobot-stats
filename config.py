"""Усі налаштування бота в одному місці."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _int_set(raw):
    result = set()
    for part in (raw or "").replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            result.add(int(part))
    return result


# --- Telegram ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_IDS = _int_set(os.getenv("ADMIN_IDS"))

# --- Джерела даних ---
SVITLOBOT_API = "https://api.svitlobot.in.ua/website/getChannelsForMap"
TRYVOHA_KYIV = "https://tryvoha.online/api/v1/alerts/kyiv"
MAPA_NEARBY = "https://mapa.ua/api/v1/nearby"
NEPTUN_THREATS = "https://neptun.in.ua/api/v1/threats"
NEPTUN_ALERTS = "https://neptun.in.ua/api/v1/alerts"

# --- Київ ---
KYIV_TZ = ZoneInfo("Europe/Kyiv")
KYIV_LAT, KYIV_LON = 50.4501, 30.5234
NEARBY_RADIUS_KM = 35  # трохи ширше, щоб покрити Київську область

# --- Світло ---
SVITLO_COOLDOWN_MIN = 15
MIN_DISTRICT_POINTS = int(os.getenv("MIN_DISTRICT_POINTS", "5"))
LOW_SAMPLE_POINTS = 10
TOP_DISTRICTS = 5
RANK_SMOOTHING = int(os.getenv("RANK_SMOOTHING", "10"))
DISTRICT_FALLBACK_MAX_KM = 8
DISTRICTS_GEOJSON = Path(os.getenv("DISTRICTS_GEOJSON", "data/districts.geojson"))

# --- Тривога ---
ALERT_REFRESH_MIN = int(os.getenv("ALERT_REFRESH_MIN", "10"))
MAX_THREAT_LINES = 10

# --- Стан ---
STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))
