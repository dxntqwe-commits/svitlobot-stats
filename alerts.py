"""Тривога: джерела, опис цілей, форматування повідомлень.
Основне джерело — NEPTUN, запасні — mapa.ua та tryvoha.online.
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
    # NEPTUN
    "uav": "БпЛА",
    "recon": "Розвідка",
    "missile": "Ракета",
    "ballistic": "Балістика",
    "kab": "КАБ",
    "mig31k": "МіГ-31К",
    "unknown": "Невідома ціль",
    # mapa.ua (запасне)
    "drone_piston": "Шахед / БпЛА",
    "drone_jet": "Реактивний БпЛА",
    "drone_fpv": "FPV",
    "missile_cruise": "Крилата ракета",
    "missile_ballistic": "Балістика",
    "aircraft": "Авіація",
    "decoy": "Приманка",
}

HEADERS = {
    "User-Agent": "SvitlobotStats/1.2 (https://github.com/dxntqwe-commits/svitlobot-stats)",
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


# ================== ДЖЕРЕЛА ==================
def get_kyiv_alert():
    """Спочатку NEPTUN, потім tryvoha."""
    # NEPTUN
    data = _get_json(NEPTUN_ALERTS)
    if data:
        # Шукаємо Київ (місто або Київська область з активними районами)
        for item in data.get("oblasts", []) + data.get("raions", []):
            name = (item.get("name") or "").lower()
            oblast = (item.get("oblast") or "").lower()
            if "київ" in name or "київ" in oblast:
                level = item.get("level") or "yellow"
                return {
                    "active": True,
                    "level": level,
                    "started_at": item.get("since"),
                    "name": item.get("name", "м. Київ"),
                    "source": "neptun",
                }
        # Якщо нічого не знайшли — тривоги по Києву немає
        return {
            "active": False,
            "level": None,
            "started_at": None,
            "name": "м. Київ",
            "source": "neptun",
        }

    # Fallback: tryvoha
    data = _get_json(TRYVOHA_KYIV)
    if data:
        return {
            "active": bool(data.get("active")),
            "level": data.get("level"),
            "started_at": data.get("started_at"),
            "name": data.get("name_uk", "м. Київ"),
            "source": "tryvoha",
        }
    return None


def _threat_from_neptun(t: dict):
    """Нормалізуємо об'єкт NEPTUN під загальний формат."""
    kind = t.get("type") or "unknown"
    amount = max(int(t.get("count") or 1), 1)
    lat = t.get("lat")
    lon = t.get("lon")
    heading = t.get("heading")
    if heading is None and isinstance(t.get("velocity"), dict):
        heading = t["velocity"].get("bearingDeg")
    speed = None
    if isinstance(t.get("velocity"), dict):
        speed = t["velocity"].get("speedKmh")

    return {
        "kind": kind,
        "amount": amount,
        "lat": lat,
        "lon": lon,
        "heading": heading,
        "speed": speed,
        "locality": t.get("locality") or "",
        "region": t.get("region") or "",
        "explanation": t.get("explanationShort") or "",
        "area_only": bool(t.get("areaOnly")),
        "source": "neptun",
        "raw": t,
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
        "raw": t,
    }


def _format_threat_line(t: dict, from_kyiv: bool = True) -> tuple:
    """Повертає (рядок, відстань_км або None)."""
    label = KIND_UA.get(t["kind"], t["kind"])
    extras = []

    if t.get("locality"):
        extras.append(t["locality"])
    elif t.get("region"):
        extras.append(t["region"])

    dist = None
    if t.get("lat") is not None and t.get("lon") is not None and not t.get("area_only"):
        dist = distance_km(KYIV_LAT, KYIV_LON, t["lat"], t["lon"])
        if from_kyiv:
            side = compass(bearing_deg(KYIV_LAT, KYIV_LON, t["lat"], t["lon"]))
            extras.append(f"~{dist:.0f} км на {side}")
        else:
            extras.append(f"~{dist:.0f} км від Києва")

    if t.get("heading") is not None:
        extras.append(f"курс на {compass(t['heading'])}")
    if t.get("speed"):
        extras.append(f"~{int(t['speed'])} км/год")

    line = f"• {t['amount']}× {label}"
    if extras:
        line += " — " + ", ".join(extras)
    if t.get("explanation"):
        line += f"\n  {t['explanation']}"
    return line, dist


def get_nearby_threats():
    """Цілі біля Києва. Спочатку NEPTUN, потім mapa.ua."""
    # --- NEPTUN ---
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
            lines = [_format_threat_line(t)[0] for t in nearby[:MAX_THREAT_LINES]]
            if len(nearby) > MAX_THREAT_LINES:
                lines.append(f"… і ще {len(nearby) - MAX_THREAT_LINES}")
            return {"summary": summary, "details": lines, "source": "neptun"}

    # --- Fallback: mapa.ua ---
    data = _get_json(
        MAPA_NEARBY,
        params={"lat": KYIV_LAT, "lon": KYIV_LON, "radius_km": NEARBY_RADIUS_KM},
    )
    if not data:
        return None

    threats = data.get("threats") or data.get("objects") or []
    active = [
        _threat_from_mapa(t) for t in threats
        if isinstance(t, dict) and (not t.get("status") or t.get("status") == "active")
    ]
    if not active:
        return {"summary": f"Цілей у радіусі {NEARBY_RADIUS_KM} км не зафіксовано", "details": [], "source": "mapa"}

    rows = []
    for t in active:
        line, dist = _format_threat_line(t)
        rows.append((line, dist or 999))
    rows.sort(key=lambda x: x[1])
    lines = [r[0] for r in rows[:MAX_THREAT_LINES]]
    if len(rows) > MAX_THREAT_LINES:
        lines.append(f"… і ще {len(rows) - MAX_THREAT_LINES}")

    counter = Counter(t["kind"] for t in active)
    summary = f"Біля Києва (~{NEARBY_RADIUS_KM} км): " + ", ".join(
        f"{cnt}× {KIND_UA.get(k, k)}" for k, cnt in counter.most_common()
    )
    return {"summary": summary, "details": lines, "source": "mapa"}


def get_threats_by_region(region_query: str):
    """Цілі в певній області (по NEPTUN)."""
    q = region_query.strip().lower()
    if not q:
        return None

    data = _get_json(NEPTUN_THREATS)
    if not data or not data.get("threats"):
        return None

    matched = []
    for raw in data["threats"]:
        if raw.get("status") != "active":
            continue
        region = (raw.get("region") or "").lower()
        locality = (raw.get("locality") or "").lower()
        if q in region or q in locality:
            matched.append(_threat_from_neptun(raw))

    if not matched:
        return {"summary": f"Цілей в «{region_query}» не зафіксовано", "details": [], "source": "neptun"}

    lines = [_format_threat_line(t, from_kyiv=False)[0] for t in matched[:MAX_THREAT_LINES]]
    if len(matched) > MAX_THREAT_LINES:
        lines.append(f"… і ще {len(matched) - MAX_THREAT_LINES}")

    counter = Counter(t["kind"] for t in matched)
    summary = f"Цілі в «{region_query}»: " + ", ".join(
        f"{cnt}× {KIND_UA.get(k, k)}" for k, cnt in counter.most_common()
    )
    return {"summary": summary, "details": lines, "source": "neptun"}


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
    src = alert.get("source", "neptun")
    if src == "neptun":
        text += "\n\nДані: NEPTUN (neptun.in.ua)"
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
    text += f"\nБув рівень: {level_text(prev_level)}\n\nДані: NEPTUN / tryvoha.online"
    return text
