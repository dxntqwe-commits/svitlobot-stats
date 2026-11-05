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
# Необов'язково: ID адмінів через кому (для команди /vidnovyty). Порожньо = може будь-хто в чаті.
ADMIN_IDS = _int_set(os.getenv("ADMIN_IDS"))

# --- Джерела даних ---
SVITLOBOT_API = "https://api.svitlobot.in.ua/website/getChannelsForMap"
TRYVOHA_KYIV = "https://tryvoha.online/api/v1/alerts/kyiv"
MAPA_NEARBY = "https://mapa.ua/api/v1/nearby"

# --- Київ ---
KYIV_TZ = ZoneInfo("Europe/Kyiv")
KYIV_LAT, KYIV_LON = 50.4501, 30.5234
NEARBY_RADIUS_KM = 20

# --- Світло ---
SVITLO_COOLDOWN_MIN = 15  # /svitlo: стара картинка видаляється, якщо їй менше N хв
MIN_DISTRICT_POINTS = int(os.getenv("MIN_DISTRICT_POINTS", "5"))  # менше точок — район не в рейтингу
LOW_SAMPLE_POINTS = 10  # менше точок — позначка "мало точок"
TOP_DISTRICTS = 5
# Згладжування рейтингу: скільки "уявних" точок із середнім по місту додається до району.
# Більше число = менше шансів, що район з кількома датчиками випадково опиниться нагорі.
RANK_SMOOTHING = int(os.getenv("RANK_SMOOTHING", "10"))
DISTRICT_FALLBACK_MAX_KM = 8  # лише для наближеного режиму районів
DISTRICTS_GEOJSON = Path(os.getenv("DISTRICTS_GEOJSON", "data/districts.geojson"))

# --- Тривога ---
ALERT_REFRESH_MIN = int(os.getenv("ALERT_REFRESH_MIN", "10"))  # як часто оновлювати цілі
MAX_THREAT_LINES = 8  # скільки цілей показувати в описі

# --- Стан ---
STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))
