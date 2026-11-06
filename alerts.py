"""Тривога: джерела, опис цілей, форматування повідомлень."""
import logging
import time
from collections import Counter

import requests

from config import (
    KYIV_LAT,
    KYIV_LON,
    MAPA_NEARBY,
    MAX_THREAT_LINES,
    NEARBY_RADIUS_KM,
    TRYVOHA_KYIV,
)
from geo import bearing_deg, compass, distance_km
from timeutils import format_duration, now_kyiv_str, ts_kyiv_str

logger = logging.getLogger(__name__)

KIND_UA = {
    "drone_piston": "Шахед / БпЛА",
    "drone_jet": "Реактивний БпЛА",
    "drone_fpv": "FPV",
    "missile_cruise": "Крилата ракета",
    "missile_ballistic": "Балістика",
    "missile": "Ракета",
    "kab": "КАБ",
    "aircraft": "Авіація",
    "recon": "Розвідка",
    "decoy": "Приманка",
}

HEADERS = {
    "User-Agent": "SvitlobotStats/1.1 (https://github.com/dxntqwe-commits/svitlobot-stats)",
    "Accept": "application/json",
}


def _get_json(url: str, params=None, retries: int = 3, timeout: int = 12):
    """Запит з ретраями і нормальним User-Agent."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            logger.warning(f"Запит {url} спроба {attempt}/{retries}: {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(1.5 * attempt)
    logger.error(f"Не вдалося отримати {url} після {retries} спроб: {last_err}")
    return None


# ================== ДЖЕРЕЛА ==================
def get_kyiv_alert():
    # Основний + запасний домен
    urls = [
        TRYVOHA_KYIV,
        "https://xn--80adds5ajn.online/api/v1/alerts/kyiv",
    ]
    for url in urls:
        data = _get_json(url)
        if data:
            return {
                "active": bool(data.get("active")),
                "level": data.get("level"),
                "started_at": data.get("started_at"),
                "name": data.get("name_uk", "м. Київ"),
            }
    return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_num(t: dict, keys):
    for k in keys:
        v = _num(t.get(k))
        if v is not None:
            return v
    return None


def _first_str(t: dict, keys):
    for k in keys:
        v = t.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _coords(t: dict):
    for src in (t, t.get("position"), t.get("location"), t.get("coords")):
        if isinstance(src, dict):
            lat = _num(src.get("lat") if src.get("lat") is not None else src.get("latitude"))
            lon = _num(
                src.get("lon") if src.get("lon") is not None
                else src.get("lng") if src.get("lng") is not None
                else src.get("longitude")
            )
            if lat is not None and lon is not None:
                return lat, lon
    return None


def _amount(t: dict) -> int:
    try:
        return max(int(t.get("amount") or 1), 1)
    except (TypeError, ValueError):
        return 1


def _threat_line(t: dict):
    """Один рядок про ціль. Повертає (текст, відстань, чи_є_деталі)."""
    kind = t.get("kind") or t.get("subkind") or "unknown"
    label = KIND_UA.get(kind, kind)
    extras = []

    place = _first_str(t, ("settlement", "place", "region", "oblast", "area", "location_name"))
    if place:
        extras.append(place)

    dist = _first_num(t, ("distance_km", "distance"))
    coords = _coords(t)
    if coords:
        lat, lon = coords
        if dist is None:
            dist = distance_km(KYIV_LAT, KYIV_LON, lat, lon)
        side = compass(bearing_deg(KYIV_LAT, KYIV_LON, lat, lon))
        extras.append(f"~{dist:.0f} км на {side} від Києва")
    elif dist is not None:
        extras.append(f"~{dist:.0f} км від Києва")

    course = _first_num(t, ("course", "heading", "azimuth"))
    if course is not None:
        extras.append(f"курс на {compass(course)}")
    else:
        direction = _first_str(t, ("direction", "destination", "target", "to"))
        if direction:
            extras.append(f"курс: {direction}")

    line = f"• {_amount(t)}× {label}"
    if extras:
        line += " — " + ", ".join(extras)
    return line, dist, bool(extras)


_sample_logged = False


def get_nearby_threats():
    """Повертає {"summary": str, "details": [str]} або None, якщо джерело недоступне."""
    global _sample_logged
    data = _get_json(
        MAPA_NEARBY,
        params={"lat": KYIV_LAT, "lon": KYIV_LON, "radius_km": NEARBY_RADIUS_KM},
    )
    if not data:
        return None

    threats = data.get("threats") or data.get("objects") or []

    if threats and not _sample_logged:
        logger.info("mapa.ua зразок цілі: %s", threats[0])
        _sample_logged = True

    active = [
        t for t in threats
        if isinstance(t, dict) and not (t.get("status") and t.get("status") != "active")
    ]
    if not active:
        return {"summary": f"Цілей у радіусі {NEARBY_RADIUS_KM} км не зафіксовано", "details": []}

    counter = Counter()
    for t in active:
        counter[t.get("kind") or t.get("subkind") or "unknown"] += _amount(t)
    summary = f"Біля Києва (~{NEARBY_RADIUS_KM} км): " + ", ".join(
        f"{cnt}× {KIND_UA.get(kind, kind)}" for kind, cnt in counter.most_common()
    )

    rows = [_threat_line(t) for t in active]
    details = []
    if any(has_extra for _, _, has_extra in rows):
        rows.sort(key=lambda x: (x[1] is None, x[1] or 0))
        lines = [line for line, _, _ in rows]
        details = lines[:MAX_THREAT_LINES]
        if len(lines) > MAX_THREAT_LINES:
            details.append(f"… і ще {len(lines) - MAX_THREAT_LINES}")
    return {"summary": summary, "details": details}


# ================== ФОРМАТУВАННЯ ==================
def level_text(level) -> str:
    return {"red": "червоний", "yellow": "жовтий"}.get(level, "невідомий")


def level_emoji(level) -> str:
    return {"red": "🔴", "yellow": "🟡"}.get(level, "🟠")


_HEADS = {
    "start": "Повітряна тривога",
    "update": "Зміна рівня тривоги",
    "refresh": "Тривога триває",
    "status": "Повітряна тривога",
}


def format_alert_message(alert: dict, threats, started_ts, kind: str) -> str:
    """kind: start | update | refresh | status. У заголовку — час ПОЧАТКУ тривоги."""
    lvl = alert.get("level")
    head_time = ts_kyiv_str(started_ts) if started_ts else now_kyiv_str()
    text = f"{level_emoji(lvl)} {head_time} | Київ | {_HEADS[kind]}\nРівень: {level_text(lvl)}"
    if kind != "start" and started_ts:
        text += f"\nТриває: {format_duration(time.time() - started_ts)}"
    if threats:
        text += f"\n\n{threats['summary']}"
        if threats["details"]:
            text += "\n" + "\n".join(threats["details"])
    if kind in ("update", "refresh"):
        text += f"\n\nОновлено: {now_kyiv_str()}"
    text += "\n\nДжерело: tryvoha.online / mapa.ua"
    return text


def format_alert_end(prev_level, started_ts, end_ts: float) -> str:
    text = f"🟢 {ts_kyiv_str(end_ts)} | Київ | Відбій повітряної тривоги"
    if started_ts and end_ts > started_ts:
        text += (
            f"\nОстання тривога: {ts_kyiv_str(started_ts)}–{ts_kyiv_str(end_ts)} "
            f"({format_duration(end_ts - started_ts)})"
        )
    text += f"\nБув рівень: {level_text(prev_level)}\n\nДжерело: tryvoha.online"
    return text
