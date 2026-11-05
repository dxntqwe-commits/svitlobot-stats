"""Визначення району Києва за координатами + рейтинг районів."""
import json
import logging
import math

from config import (
    DISTRICTS_GEOJSON,
    DISTRICT_FALLBACK_MAX_KM,
    MIN_DISTRICT_POINTS,
    RANK_SMOOTHING,
)
from geo import distance_km

logger = logging.getLogger(__name__)

# Наближені центри районів (lat, lon). Працюють, якщо немає data/districts.geojson.
DISTRICT_CENTERS = {
    "Святошинський": (50.4575, 30.3650),
    "Шевченківський": (50.4630, 30.4600),
    "Солом'янський": (50.4250, 30.4400),
    "Голосіївський": (50.3800, 30.5000),
    "Печерський": (50.4300, 30.5450),
    "Подільський": (50.4950, 30.4700),
    "Оболонський": (50.5250, 30.5000),
    "Деснянський": (50.5250, 30.6200),
    "Дніпровський": (50.4600, 30.6000),
    "Дарницький": (50.4000, 30.6500),
}


def _norm_name(name: str) -> str:
    name = (name or "").replace("’", "'").replace("ʼ", "'").replace("`", "'")
    for suffix in (" район", " р-н"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _point_in_ring(lon, lat, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon, lat, rings) -> bool:
    if not rings or not _point_in_ring(lon, lat, rings[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in rings[1:])


def _load_polygons():
    """GeoJSON (Polygon/MultiPolygon, координати [lon, lat]) -> [(name, [rings...])]."""
    if not DISTRICTS_GEOJSON.exists():
        return None
    try:
        data = json.loads(DISTRICTS_GEOJSON.read_text(encoding="utf-8"))
        result = []
        for f in data.get("features", []):
            props = f.get("properties") or {}
            name = (
                props.get("name:uk") or props.get("name_uk")
                or props.get("name") or props.get("district")
            )
            geom = f.get("geometry") or {}
            if not name:
                continue
            if geom.get("type") == "Polygon":
                polys = [geom["coordinates"]]
            elif geom.get("type") == "MultiPolygon":
                polys = geom["coordinates"]
            else:
                continue
            result.append((_norm_name(name), polys))
        if result:
            logger.info(f"Завантажено {len(result)} районів з {DISTRICTS_GEOJSON}")
            return result
    except Exception as e:
        logger.warning(f"Не вдалося прочитати {DISTRICTS_GEOJSON}: {e}")
    return None


DISTRICT_POLYGONS = _load_polygons()
EXACT_DISTRICTS = DISTRICT_POLYGONS is not None


def classify_district(lat: float, lon: float):
    if DISTRICT_POLYGONS:
        for name, polys in DISTRICT_POLYGONS:
            if any(_point_in_polygon(lon, lat, rings) for rings in polys):
                return name
        return None
    best, best_d = None, math.inf
    for name, (clat, clon) in DISTRICT_CENTERS.items():
        d = distance_km(lat, lon, clat, clon)
        if d < best_d:
            best, best_d = name, d
    return best if best_d <= DISTRICT_FALLBACK_MAX_KM else None


def rank_districts(stats: dict):
    """(надійні райони, райони з малою вибіркою).

    Райони різні за кількістю датчиків, тому "сирий" відсоток обманює: 2 з 3 без світла
    виглядає гірше, ніж 40 з 70. Щоб район з кількома точками не потрапляв нагору випадково,
    сортуємо за ЗГЛАДЖЕНОЮ часткою: до району додається RANK_SMOOTHING "уявних" точок
    із середнім по місту. Чим більше реальних датчиків — тим менше впливає згладжування.
    У тексті і на картинці показується звичайний відсоток та "N з M".
    """
    all_total = sum(c["on"] + c["off"] for c in stats["districts"].values())
    all_off = sum(c["off"] for c in stats["districts"].values())
    city_rate = all_off / all_total if all_total else 0.0

    reliable, small = [], []
    for name, c in stats["districts"].items():
        total = c["on"] + c["off"]
        row = {
            "name": name,
            "on": c["on"],
            "off": c["off"],
            "total": total,
            "off_pct": round(c["off"] / total * 100, 1) if total else 0.0,
            "score": (c["off"] + RANK_SMOOTHING * city_rate) / (total + RANK_SMOOTHING),
        }
        (reliable if total >= MIN_DISTRICT_POINTS else small).append(row)
    reliable.sort(key=lambda r: (-r["score"], -r["total"]))
    small.sort(key=lambda r: -r["total"])
    return reliable, small
