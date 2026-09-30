"""
Describe en palabras una ubicación compartida por WhatsApp (latitud/longitud), en el orden que
le sirve a quien despacha: primero el lugar (barrio), después la calle que de verdad queda más
cerca del pin y por último una referencia cercana (escuela, iglesia, parque, supermercado,
restaurante conocido).

Fuentes, todas gratuitas y sobre los mismos mapas de OpenStreetMap, consultadas a la vez y con
tiempo máximo corto (la respuesta al cliente no espera más de unos segundos):
- Photon (komoot): rápido y estable. Da los lugares más cercanos con la calle de cada uno (la
  calle que más se repite entre los lugares vecinos es la calle del pin) y las calles cercanas.
- Overpass: la geometría exacta de las calles, para medir cuál pasa más cerca del punto. Es el
  más preciso pero a veces tarda o rechaza; cuando responde, manda.
- Nominatim: barrio, corregimiento y ciudad, y el nombre del comercio si el pin cae encima.

Si todo falla, el bot sigue sin descripción (nunca lanza). El pin exacto se guarda aparte.
"""
import asyncio
import logging
from collections import defaultdict
from math import cos, radians, sqrt
from typing import Optional

import httpx

logger = logging.getLogger("farmhouse.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
PHOTON_URL = "https://photon.komoot.io/reverse"
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "FarmhouseLink/1.0 (farmhousepanelwhatsapp-production.up.railway.app)"
TIMEOUT_SECONDS = 6.0
OVERPASS_TIMEOUT_SECONDS = 6.0
STREET_RADIUS_M = 120
POI_STREET_RADIUS_M = 150
LANDMARK_RADIUS_M = 250

# Tipo de lugar -> cómo se dice en el mensaje. La prioridad (número más bajo primero) decide
# entre dos referencias a distancia parecida: una escuela o iglesia orienta más que un banco.
LANDMARK_KINDS = {
    "school": ("escuela", 0), "college": ("colegio", 0), "university": ("universidad", 0),
    "place_of_worship": ("iglesia", 0), "park": ("parque", 0), "hospital": ("hospital", 0),
    "supermarket": ("supermercado", 1), "wholesale": ("supermercado", 1), "mall": ("centro comercial", 1),
    "stadium": ("estadio", 1), "fuel": ("gasolinera", 1), "police": ("policía", 1), "townhall": ("municipio", 1),
    "clinic": ("clínica", 2), "pharmacy": ("farmacia", 2), "bank": ("banco", 2),
    "marketplace": ("mercado", 2), "community_centre": ("centro comunitario", 2),
    "sports_centre": ("centro deportivo", 2), "pitch": ("cancha", 2), "department_store": ("tienda", 2),
    "restaurant": ("restaurante", 2), "fast_food": ("restaurante", 2), "cafe": ("café", 2), "hotel": ("hotel", 2),
}
LANDMARK_KEYS = ("amenity", "leisure", "shop", "tourism")


def _overpass_query(latitude: float, longitude: float) -> str:
    amenities = "school|college|university|place_of_worship|hospital|clinic|pharmacy|bank|police|fuel|marketplace|community_centre|townhall"
    return (
        f"[out:json][timeout:5];("
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
    """Distancia del punto al elemento: a la línea (calle con geometría) o al punto (lugar)."""
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
    """{"name", "distance_m"} de la calle con nombre más cercana al punto, o None. Las calles
    con geometría (Overpass) se miden a la línea; una calle que llega como punto (Photon) se
    mide a ese punto, que es menos preciso, así que solo se usa si no hay ninguna con línea."""
    best_line = best_point = None
    for e in elements:
        tags = e.get("tags") or {}
        if not tags.get("highway") or not tags.get("name"):
            continue
        d = _element_distance_m(e, latitude, longitude)
        if d is None:
            continue
        if e.get("type") == "way" and e.get("geometry"):
            if d <= STREET_RADIUS_M and (best_line is None or d < best_line["distance_m"]):
                best_line = {"name": tags["name"], "distance_m": round(d)}
        elif best_point is None or d < best_point["distance_m"]:
            best_point = {"name": tags["name"], "distance_m": round(d)}
    return best_line or best_point


def street_from_pois(elements: list, latitude: float, longitude: float) -> Optional[dict]:
    """La calle que más se repite entre los lugares vecinos (cada lugar de OpenStreetMap trae su
    calle), pesando más los más cercanos. Cinco comercios a 60 m sobre "Carretera Hospital" dicen
    más que el centro de una calle vecina a 150 m."""
    puntaje = defaultdict(float)
    cercania = {}
    for e in elements:
        tags = e.get("tags") or {}
        street = tags.get("street")
        if not street or tags.get("highway"):
            continue
        d = _element_distance_m(e, latitude, longitude)
        if d is None or d > POI_STREET_RADIUS_M:
            continue
        puntaje[street] += 1.0 / (d + 10.0)
        cercania[street] = min(d, cercania.get(street, d))
    if not puntaje:
        return None
    name = max(puntaje, key=puntaje.get)
    return {"name": name, "distance_m": round(cercania[name])}


def nearest_landmark(elements: list, latitude: float, longitude: float) -> Optional[dict]:
    """{"name", "kind", "distance_m"} del lugar de referencia que mejor orienta: el más cercano,
    con ventaja para escuelas, iglesias y parques (ver LANDMARK_KINDS)."""
    best = None
    for e in elements:
        tags = e.get("tags") or {}
        if not tags.get("name") or tags.get("highway"):
            continue
        raw_kind = next((tags.get(k) for k in LANDMARK_KEYS if tags.get(k)), None)
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
    """Arma la descripción. `address`/`name`/`category` vienen de Nominatim (o del equivalente
    armado con Photon); `street` y `landmark` de las funciones de arriba. Sin red, para probar."""
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

    # Un pin sobre un comercio, edificio o amenidad trae su nombre.
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
    """Solo con Nominatim (sin calles ni lugares vecinos)."""
    return compose(address, name, category)


def photon_to_elements(features: list) -> list:
    """Convierte las respuestas de Photon (GeoJSON) a la forma de elemento que usan las
    funciones de arriba: un punto con sus etiquetas (name, highway/amenity/leisure/shop, street)."""
    out = []
    for f in features or []:
        p = f.get("properties") or {}
        geom = (f.get("geometry") or {}).get("coordinates") or []
        if len(geom) != 2:
            continue
        tags = {"name": p.get("name"), "street": p.get("street")}
        key, value = p.get("osm_key"), p.get("osm_value")
        if key in ("highway",) + LANDMARK_KEYS and value:
            tags[key] = value
        out.append({"type": "node", "lat": float(geom[1]), "lon": float(geom[0]), "tags": tags, "props": p})
    return out


def address_from_photon(elements: list) -> dict:
    """Barrio, corregimiento y ciudad a partir del lugar vecino más cercano, cuando Nominatim no
    respondió. Mismas llaves que Nominatim para que compose() no distinga."""
    for e in elements:
        p = e.get("props") or {}
        if p.get("locality") or p.get("district") or p.get("city"):
            return {
                "road": p.get("street") or "", "neighbourhood": p.get("locality") or "",
                "suburb": p.get("district") or "", "city": p.get("city") or "", "county": p.get("county") or "",
            }
    return {}


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
        logger.warning("[Geocoding] Nominatim no respondió para (%s, %s).", latitude, longitude)
        return None


async def _photon(latitude: float, longitude: float, layer: Optional[str] = None, limit: int = 8) -> list:
    params = {"lat": latitude, "lon": longitude, "limit": limit}
    if layer:
        params["layer"] = layer
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.get(PHOTON_URL, params=params, headers={"User-Agent": USER_AGENT})
            res.raise_for_status()
            data = res.json()
        return photon_to_elements((data or {}).get("features") or [])
    except Exception:
        logger.warning("[Geocoding] Photon (%s) no respondió para (%s, %s).", layer or "lugares", latitude, longitude)
        return []


async def _overpass_one(url: str, query: str) -> list:
    async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_SECONDS) as client:
        res = await client.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT})
        res.raise_for_status()
        data = res.json()
    return list(data.get("elements") or [])


async def _overpass(latitude: float, longitude: float) -> list:
    """Los dos espejos a la vez; gana el primero que responda con datos. Si ninguno llega a
    tiempo, se sigue con Photon: Overpass es el más preciso pero el menos confiable."""
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
    return []


async def reverse_geocode(latitude: float, longitude: float) -> Optional[dict]:
    """Descripción del punto (ver compose), o None si no se pudo describir nada."""
    nominatim, pois, streets, overpass = await asyncio.gather(
        _nominatim(latitude, longitude),
        _photon(latitude, longitude, limit=10),
        _photon(latitude, longitude, layer="street", limit=4),
        _overpass(latitude, longitude),
    )
    address = (nominatim or {}).get("address") or address_from_photon(pois)
    if not address and not pois and not streets and not overpass:
        return None

    # Calle: geometría de Overpass si llegó; si no, la calle de los lugares vecinos; si no, la
    # calle más cercana de Photon; compose() cae a la de Nominatim como último recurso.
    street = nearest_street(overpass, latitude, longitude) if overpass else None
    if street is None:
        street = street_from_pois(pois, latitude, longitude) or nearest_street(streets, latitude, longitude)
    landmark = nearest_landmark(overpass + pois, latitude, longitude)

    described = compose(address, (nominatim or {}).get("name"), (nominatim or {}).get("category"), street=street, landmark=landmark)
    return described if described["label"] or described["full_address"] else None
