"""Стан бота в одному JSON-файлі (без бази даних)."""
import copy
import json
import logging

from config import STATE_FILE

logger = logging.getLogger(__name__)

DEFAULT_STATE = {
    "last_alert_active": False,
    "last_alert_level": None,
    "alert_message_id": None,  # пост про тривогу (активну або останню завершену)
    "alert_started_ts": None,
    "alert_last_refresh_ts": 0,
    "last_svitlo_message_id": None,
    "last_svitlo_ts": 0,
    "daily": {},  # {"2026-11-06": {"on":..,"off":..,"total":..}} — замір о 12:05
    "last_yasno_updated": None,  # ISO timestamp останнього оновлення графіка
    "last_yasno_tomorrow_date": None,  # дата, для якої вже відправили графік на завтра
}


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {**copy.deepcopy(DEFAULT_STATE), **loaded}
    except Exception as e:
        logger.warning(f"Не вдалося прочитати state: {e}")
    return copy.deepcopy(DEFAULT_STATE)


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Не вдалося зберегти state: {e}")


# Один спільний об'єкт: модулі імпортують його і лише змінюють (не переприсвоюють).
state = load_state()


def get_daily(date_str: str):
    return (state.get("daily") or {}).get(date_str)


def set_daily(date_str: str, on: int, off: int) -> None:
    daily = state.setdefault("daily", {})
    daily[date_str] = {"on": on, "off": off, "total": on + off}
    for old in sorted(daily)[:-7]:  # лишаємо останні 7 днів
        daily.pop(old, None)
    save_state(state)
