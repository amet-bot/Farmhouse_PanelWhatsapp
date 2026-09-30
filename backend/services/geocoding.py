"""
Describe en palabras una ubicación compartida por WhatsApp (latitud/longitud), en el orden que
le sirve a quien despacha: primero el lugar (barrio), después la calle que de verdad queda más
cerca del pin y por último una referencia cercana (escuela, iglesia, parque, supermercado).

Dos consultas a OpenStreetMap, sin clave:
- Nominatim (reverse): barrio, corregimiento, ciudad y, si el pin cae sobre un comercio o
  edificio, su nombre. Su "calle" es la del edificio más cercano, que a veces es la de al lado.
- Overpass: las calles con nombre a menos de 120 m y los lugares de referencia a menos de
  250 m, con su geometría, para medir cuál queda más cerca del punto exacto.

Es una ayuda para el panel y el motorizado: si algo falla o tarda, el bot sigue sin descripción
(nunca lanza). El pin exacto (enlace de Maps) se guarda siempre aparte.
"""
import asyncio
import logging
from math import cos, radians, sqrt
from typing import Optional

import httpx

logger = logging.getLogger("farmhouse.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "FarmhouseLink/1.0 (farmhousepanelwhatsapp-production.up.railway.app)"
TIMEOUT_SECONDS = 8.0
OVERPASS_TIMEOUT_SECONDS = 14.0
STREET_RADIUS_M = 120
LANDMARK_RADIUS_M = 250

# Tipo de lugar -> cómo se dice en el mensaje. La prioridad (número más bajo primero) decide
# entre dos referencias a distancia parecida: una escuela o iglesia orienta más que un banco.
LANDMARK_KINDS = {
    "school": ("escuela", 0), "college": ("colegio", 0), "university": ("universidad", 0),
    "place_of_worship": ("iglesia", 0), "park": ("parque", 0), "hospital": ("hospital", 0),
    "supermarket": ("supermercado", 1), "mall": ("centro comercial", 1), "stadium": ("estadio", 1),
    "fuel": ("gasolinera", 1), "police": ("policía", 1), "townhall": ("municipio", 1),
    "clinic": ("clínica", 2), "pharmacy": ("farmacia", 2), "bank": ("banco", 2),
    "marketplace": ("mercado", 2), "community_centre": ("centro comunitario", 2),
    "sports_centre": ("centro deportivo", 2), "pitch": ("cancha", 2), "department_store": ("tienda", 2),
}


def _overpass_query(latitude: float, longitude: float) -> str:
    amenities = "school|college|university|place_of_worship|hospital|clinic|pharmacy|bank|police|fuel|marketplace|community_centre|townhall"
    return (
        f"[out:json][timeout:12];("
        f"way(around:{STREET_RADIUS_M},{latitude},{longitude})[highway][name];"
        f'nwr(around:{LANDMARK_RADIUS_M},{latitude},{longitude})[name][amenity~"{amenities}"];'
        f'nwr(around:{LANDMARK_RADIUS_M},{latitude},{longitude})[name][leisure~"park|sports_centre|stadium|pitch"];'
        f'nwr(around:{LANDMARK_RADIUS_M},{latitude},{longitude})[name][shop~"supermarket|mall|department_store"];'
        f");out geom;"
    )


# ---- geometría (proyección plana local: suficiente para cientos de metros) ----

def _to_meters(lat: float, lng: float, lat0: float, lng0: float) -> tuple:
    return ((lng - lng0) * 111320.0 * cos(radians(lat0)), (lat - lat0) * 110540.0)


def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return sqrt((px - cx) ** 2 + (py - cy) ** 2)


def _element_distance_m(element: dict, latitude: float, longitude: float) -> Optional[float]:
    """Distancia del punto al elemento: a la línea (calle) o al vértice más cercano (lugar)."""
    if element.get("type") == "node" and "lat" in element:
        x, y = _to_meters(element["lat"], element["lon"], latitude, longitude)
        return sqrt(x * x + y * y)
    pts = [(g["lat"], g["lon"]) for g in (element.get("geometry") or []) if "lat" in g and "lon" in g]
    if not pts and element.get("center"):
        pts = [(element["center"]["lat"], element["center"]["lon"])]
    if not pts:
        return None
    xy = [_to_meters(la, lo, latitude, longitude) for la, lo in pts]
    if len(xy) == 1:
        return sqrt(xy[0][0] ** 2 + xy[0][1] ** 2)
    return min(_point_segment_distance(0.0, 0.0, ax, ay, bx, by) for (ax, ay), (bx, by) in zip(xy, xy[1:]))


def nearest_street(elements: list, latitude: float, longitude: float) -> Optional[dict]:
    """{"name", "distance_m"} de la calle con nombre más cercana al punto, o None."""
    best = None
    for e in elements:
        tags = e.get("tags") or {}
        if e.get("type") != "way" or not tags.get("highway") or not tags.get("name"):
            continue
        d = _element_distance_m(e, latitude, longitude)
        if d is None or d > STREET_RADIUS_M:
            continue
        if best is None or d < best["distance_m"]:
            best = {"name": tags["name"], "distance_m": round(d)}
    return best


def nearest_landmark(elements: list, latitude: float, longitude: float) -> Optional[dict]:
    """{"name", "kind", "distance_m"} del lugar de referencia que mejor orienta: el más cercano,
    con ventaja para escuelas, iglesias y parques (ver LANDMARK_KINDS)."""
    best = None
    for e in elements:
        tags = e.get("tags") or {}
        if not tags.get("name") or tags.get("highway"):
            continue
        raw_kind = tags.get("amenity") or tags.get("leisure") or tags.get("shop")
        if raw_kind not in LANDMARK_KINDS:
            continue
        d = _element_distance_m(e, latitude, longitude)
        if d is None or d > LANDMARK_RADIUS_M:
            continue
        kind, priority = LANDMARK_KINDS[raw_kind]
        score = d + priority * 80
        if best is None or score < best["_score"]:
            best = {"name": tags["name"], "kind": kind, "distance_m": round(d), "_score": score}
    if best:
        best.pop("_score")
    return best


# ---- composición ----

def compose(address: dict, name: Optional[str], category: Optional[str], street: Optional[dict] = None, landmark: Optional[dict] = None) -> dict:
    """Arma la descripción. `address`/`name`/`category` vienen de Nominatim; `street` y
    `landmark` de nearest_street/nearest_landmark (opcionales). Separado para probarlo sin red."""
    area = address.get("neighbourhood") or address.get("quarter") or address.get("suburb") or address.get("village") or address.get("city_district") or ""
    district = address.get("suburb") or address.get("city_district") or address.get("town") or ""
    if district == area:
        district = address.get("city_district") if address.get("city_district") != area else ""
    city_hint = address.get("county") or address.get("city") or address.get("state") or ""
    city = "Ciudad de Panamá" if "Panamá" in city_hint else (address.get("city") or "")

    nominatim_road = address.get("road") or address.get("pedestrian") or address.get("residential") or ""
    house_number = address.get("house_number") or ""
    street_name = (street or {}).get("name") or nominatim_road
    street_part = f"{street_name} {house_number}".strip() if street_name == nominatim_road else street_name

    # Un pin sobre un comercio, edificio o amenidad trae su nombre en Nominatim.
    place = ""
    if category in ("shop", "amenity", "building", "office", "tourism", "leisure") and name and name != nominatim_road:
        place = name
    if not place:
        place = address.get("building") or ""

    if landmark:
        nearby = f"{'en' if landmark['distance_m'] <= 30 else 'cerca de'} {landmark['name']}"
        landmark_text = f"{landmark['name']} ({landmark['kind']}, a {landmark['distance_m']} m)"
    elif place:
        nearby = f"en {place}"
        landmark_text = place
    else:
        nearby = ""
        landmark_text = ""

    label_parts = [p for p in (area, street_part) if p]
    label = ", ".join(label_parts)
    if nearby:
        label = f"{label}, {nearby}" if label else nearby

    full_parts = []
    for p in (street_part, area, district, city):
        if p and p not in full_parts:
            full_parts.append(p)
    return {
        "street": street_part, "area": area, "place": place, "label": label,
        "full_address": ", ".join(full_parts), "landmark": landmark, "landmark_text": landmark_text,
    }


def describe(address: dict, name: Optional[str], category: Optional[str]) -> dict:
    """Solo con Nominatim (sin Overpass)."""
    return compose(address, name, category)


# ---- red ----

async def _nominatim(latitude: float, longitude: float) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.get(NOMINATIM_URL, params={
                "format": "jsonv2", "lat": latitude, "lon": longitude, "zoom": 18,
                "addressdetails": 1, "accept-language": "es",
            }, headers={"User-Agent": USER_AGENT})
            res.raise_for_status()
            data = res.json()
        return data if isinstance(data, dict) and "address" in data else None
    except Exception:
        logger.warning("[Geocoding] Nominatim no respondió para (%s, %s).", latitude, longitude, exc_info=True)
        return None


async def _overpass_one(url: str, query: str) -> list:
    async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_SECONDS) as client:
        res = await client.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT})
        res.raise_for_status()
        data = res.json()
    return list(data.get("elements") or [])


async def _overpass(latitude: float, longitude: float) -> list:
    """Consulta los dos espejos a la vez y se queda con el primero que responda con datos: los
    servidores públicos de Overpass a veces tardan o rechazan, y el cliente está esperando."""
    query = _overpass_query(latitude, longitude)
    tasks = [asyncio.ensure_future(_overpass_one(url, query)) for url in OVERPASS_URLS]
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.exception() and task.result():
                    return task.result()
    finally:
        for task in tasks:
            task.cancel()
    logger.warning("[Geocoding] Overpass no respondió para (%s, %s).", latitude, longitude)
    return []


async def reverse_geocode(latitude: float, longitude: float) -> Optional[dict]:
    """Descripción del punto (ver compose), o None si no se pudo describir nada."""
    nominatim, elements = await asyncio.gather(_nominatim(latitude, longitude), _overpass(latitude, longitude))
    address = (nominatim or {}).get("address") or {}
    if not address and not elements:
        return None
    described = compose(
        address, (nominatim or {}).get("name"), (nominatim or {}).get("category"),
        street=nearest_street(elements, latitude, longitude),
        landmark=nearest_landmark(elements, latitude, longitude),
    )
    return described if described["label"] or described["full_address"] else None
