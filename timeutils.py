"""Час за Києвом, тривалість, розбір часу з API."""
import time
from datetime import datetime

from config import KYIV_TZ


def now_kyiv() -> datetime:
    return datetime.now(KYIV_TZ)


def now_kyiv_str() -> str:
    return now_kyiv().strftime("%H:%M")


def ts_kyiv_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, KYIV_TZ).strftime("%H:%M")


def kyiv_date_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, KYIV_TZ).strftime("%Y-%m-%d")


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "менше хвилини"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} год {m} хв"
    if h:
        return f"{h} год"
    return f"{m} хв"


def parse_started_at(value):
    """Розбирає started_at з API. Повертає unix-ts або None."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KYIV_TZ)
            ts = dt.timestamp()
    except Exception:
        return None
    now = time.time()
    if ts > now + 60 or ts < now - 48 * 3600:  # захист від сміття
        return None
    return ts
