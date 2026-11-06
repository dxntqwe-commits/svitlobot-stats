"""Тривога: джерела, опис цілей, форматування повідомлень.
Основне джерело цілей — NEPTUN, тривоги по Києву — tryvoha (точніше для міста).
"""
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
    NEPTUN_ALERTS,
    NEPTUN_THREATS,
    TRYVOHA_KYIV,
)
from geo import bearing_deg, compass, distance_km
from timeutils import format_duration, now_kyiv_str, ts_kyiv_str

logger = logging.getLogger(__name__)

KIND_UA = {
    "uav": "БпЛА",
    "recon": "Розвідка",
    "missile": "Ракета",
    "ballistic": "Балістика",
    "kab": "КАБ",
    "mig31k": "МіГ-31К",
    "unknown": "Невідома ціль",
    "drone_piston": "Шахед / БпЛА",
    "drone_jet": "Реактивний БпЛА",
    "drone_fpv": "FPV",
    "missile_cruise": "Крилата ракета",
    "missile_ballistic": "Балістика",
    "aircraft": "Авіація",
    "decoy": "Приманка",
}

HEADERS = {
    "User-Agent": "SvitlobotStats/1.4 (https://github.com/dxntqwe-commits/svitlobot-stats)",
    "Accept": "application/json",
}


def _get_json(url: str, params=None, retries: int = 3, timeout: int = 12):
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


def get_kyiv_alert():
    """Тривога саме по місту Київ. Спочатку tryvoha (точніше), потім NEPTUN."""
    # tryvoha — краще для міста
    data = _get_json(TRYVOHA_KYIV)
    if data:
        return {
            "active": bool(data.get("active")),
            "level": data.get("level"),
            "started_at": data.get("started_at"),
            "name": data.get("name_uk", "м. Київ"),
            "source": "tryvoha",
        }

    # Fallback: NEPTUN — тільки якщо є саме місто, а не райони області
    data = _get_json(NEPTUN_ALERTS)
    if data:
        for item in data.get("oblasts", []) + data.get("raions", []):
            name = (item.get("name") or "").lower()
            # Тільки точне місто Київ, не «Київська область» і не райони
            if name in ("київ", "м. київ", "місто київ"):
                return {
                    "active": True,
                    "level": item.get("level") or "yellow",
                    "started_at": item.get("since"),
                    "name": item.get("name", "м. Київ"),
                    "source": "neptun",
                }
        return {
            "active": False,
            "level": None,
            "started_at": None,
            "name": "м. Київ",
            "source": "neptun",
        }
    return None


def _threat_from_neptun(t: dict):
    kind = t.get("type") or "unknown"
    amount = max(int(t.get("count") or 1), 1)
    heading = t.get("heading")
    if heading is None and isinstance(t.get("velocity"), dict):
        heading = t["velocity"].get("bearingDeg")
    speed = None
    if isinstance(t.get("velocity"), dict):
        speed = t["velocity"].get("speedKmh")
    return {
        "kind": kind,
        "amount": amount,
        "lat": t.get("lat"),
        "lon": t.get("lon"),
        "heading": heading,
        "speed": speed,
        "locality": t.get("locality") or "",
        "region": t.get("region") or "",
        "explanation": t.get("explanationShort") or "",
        "area_only": bool(t.get("areaOnly")),
        "source": "neptun",
    }


def _threat_from_mapa(t: dict):
    return {
        "kind": t.get("kind") or t.get("subkind") or "unknown",
        "amount": max(int(t.get("amount") or 1), 1),
        "lat": t.get("lat"),
        "lon": t.get("lon"),
        "heading": t.get("heading"),
        "speed": None,
        "locality": t.get("settlement") or t.get("place") or "",
        "region": t.get("region") or "",
        "explanation": "",
        "area_only": False,
        "source": "mapa",
    }


def _format_threat_line(t: dict) -> str:
    """Чіткий формат: кількість × тип — населений пункт / область, курс, швидкість."""
    label = KIND_UA.get(t["kind"], t["kind"])
    parts = []

    # Де
    if t.get("locality"):
        parts.append(t["locality"])
    if t.get("region") and t["region"] not in (t.get("locality") or ""):
        parts.append(t["region"])
    where = ", ".join(parts) if parts else "координати невідомі"

    extras = []
    if t.get("lat") is not None and t.get("lon") is not None and not t.get("area_only"):
        dist = distance_km(KYIV_LAT, KYIV_LON, t["lat"], t["lon"])
        side = compass(bearing_deg(KYIV_LAT, KYIV_LON, t["lat"], t["lon"]))
        extras.append(f"~{dist:.0f} км {side}")
    if t.get("heading") is not None:
        extras.append(f"курс {compass(t['heading'])}")
    if t.get("speed"):
        extras.append(f"{int(t['speed'])} км/год")

    line = f"• {t['amount']}× {label} — {where}"
    if extras:
        line += " (" + ", ".join(extras) + ")"
    if t.get("explanation"):
        line += f"\n  {t['explanation']}"
    return line


def get_nearby_threats():
    """Цілі біля Києва (радіус)."""
    data = _get_json(NEPTUN_THREATS)
    if data and data.get("threats"):
        normalized = [_threat_from_neptun(t) for t in data["threats"] if t.get("status") == "active"]
        nearby = []
        for t in normalized:
            if t.get("area_only") or t.get("lat") is None:
                continue
            dist = distance_km(KYIV_LAT, KYIV_LON, t["lat"], t["lon"])
            if dist <= NEARBY_RADIUS_KM:
                t["_dist"] = dist
                nearby.append(t)
        if nearby:
            nearby.sort(key=lambda x: x["_dist"])
            counter = Counter()
            for t in nearby:
                counter[t["kind"]] += t["amount"]
            summary = f"Біля Києва (~{NEARBY_RADIUS_KM} км): " + ", ".join(
                f"{cnt}× {KIND_UA.get(k, k)}" for k, cnt in counter.most_common()
            )
            lines = [_format_threat_line(t) for t in nearby[:MAX_THREAT_LINES]]
            if len(nearby) > MAX_THREAT_LINES:
                lines.append(f"… і ще {len(nearby) - MAX_THREAT_LINES}")
            return {"summary": summary, "details": lines, "source": "neptun"}

    # Fallback mapa
    data = _get_json(MAPA_NEARBY, params={"lat": KYIV_LAT, "lon": KYIV_LON, "radius_km": NEARBY_RADIUS_KM})
    if not data:
        return None
    threats = data.get("threats") or data.get("objects") or []
    active = [_threat_from_mapa(t) for t in threats if isinstance(t, dict) and (not t.get("status") or t.get("status") == "active")]
    if not active:
        return {"summary": f"Цілей у радіусі {NEARBY_RADIUS_KM} км не зафіксовано", "details": [], "source": "mapa"}
    lines = [_format_threat_line(t) for t in active[:MAX_THREAT_LINES]]
    counter = Counter(t["kind"] for t in active)
    summary = f"Біля Києва (~{NEARBY_RADIUS_KM} км): " + ", ".join(
        f"{cnt}× {KIND_UA.get(k, k)}" for k, cnt in counter.most_common()
    )
    return {"summary": summary, "details": lines, "source": "mapa"}


def get_kyiv_oblast_threats():
    """Цілі по Києву та Київській області (по назві)."""
    data = _get_json(NEPTUN_THREATS)
    if not data or not data.get("threats"):
        return None

    matched = []
    for raw in data["threats"]:
        if raw.get("status") != "active":
            continue
        region = (raw.get("region") or "").lower()
        locality = (raw.get("locality") or "").lower()
        if "київ" in region or "київ" in locality:
            matched.append(_threat_from_neptun(raw))

    if not matched:
        return {"summary": "Цілей по Києву та Київській області не зафіксовано", "details": [], "source": "neptun"}

    lines = [_format_threat_line(t) for t in matched[:MAX_THREAT_LINES]]
    if len(matched) > MAX_THREAT_LINES:
        lines.append(f"… і ще {len(matched) - MAX_THREAT_LINES}")

    counter = Counter(t["kind"] for t in matched)
    summary = "Цілі по Києву та Київській області: " + ", ".join(
        f"{cnt}× {KIND_UA.get(k, k)}" for k, cnt in counter.most_common()
    )
    return {"summary": summary, "details": lines, "source": "neptun"}


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
    lvl = alert.get("level")
    head_time = ts_kyiv_str(started_ts) if started_ts else now_kyiv_str()
    text = f"{level_emoji(lvl)} {head_time} | Київ | {_HEADS[kind]}\nРівень: {level_text(lvl)}"
    if kind != "start" and started_ts:
        text += f"\nТриває: {format_duration(time.time() - started_ts)}"
    if threats:
        text += f"\n\n{threats['summary']}"
        if threats.get("details"):
            text += "\n" + "\n".join(threats["details"])
    if kind in ("update", "refresh"):
        text += f"\n\nОновлено: {now_kyiv_str()}"
    src = alert.get("source", "tryvoha")
    if src == "neptun":
        text += "\n\nДані: NEPTUN"
    else:
        text += "\n\nДжерело: tryvoha.online"
    return text


def format_alert_end(prev_level, started_ts, end_ts: float) -> str:
    text = f"🟢 {ts_kyiv_str(end_ts)} | Київ | Відбій повітряної тривоги"
    if started_ts and end_ts > started_ts:
        text += (
            f"\nОстання тривога: {ts_kyiv_str(started_ts)}–{ts_kyiv_str(end_ts)} "
            f"({format_duration(end_ts - started_ts)})"
        )
    text += f"\nБув рівень: {level_text(prev_level)}\n\nДані: tryvoha.online / NEPTUN"
    return text
