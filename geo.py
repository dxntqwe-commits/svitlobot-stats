"""Невеликі геометричні хелпери (відстань, азимут, сторони світу)."""
import math

_COMPASS = ["пн", "пн-сх", "сх", "пд-сх", "пд", "пд-зх", "зх", "пн-зх"]


def distance_km(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * 111.0
    dx = (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Азимут від точки 1 до точки 2 у градусах (0 = північ, 90 = схід)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(deg: float) -> str:
    return _COMPASS[int(((deg % 360) + 22.5) // 45) % 8]
