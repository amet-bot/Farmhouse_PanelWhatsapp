"""
Describe en palabras una ubicación compartida por WhatsApp (latitud/longitud): calle, barrio y,
si el pin cae sobre un lugar conocido, su nombre. Usa OpenStreetMap (Nominatim), el mismo
servicio que ya usa el Menú Digital en el navegador; no necesita clave. Es solo una ayuda para
quien atiende y para el motorizado: si falla o no responde a tiempo, el bot sigue igual sin
descripción (nunca lanza).
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("farmhouse.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "FarmhouseLink/1.0 (farmhousepanelwhatsapp-production.up.railway.app)"
TIMEOUT_SECONDS = 6.0


def describe(address: dict, name: Optional[str], category: Optional[str]) -> dict:
    """Arma la descripción a partir de la respuesta de Nominatim (separado para poder probarlo
    sin red). Devuelve {"street", "area", "place", "label"}; `label` es la frase corta."""
    street = address.get("road") or address.get("pedestrian") or address.get("residential") or ""
    area = address.get("neighbourhood") or address.get("quarter") or address.get("suburb") or address.get("city_district") or ""
    # Un pin sobre un comercio, edificio o amenidad trae su nombre; una calle suelta no.
    place = ""
    if category in ("shop", "amenity", "building", "office", "tourism", "leisure") and name and name != street:
        place = name
    if not place:
        place = address.get("building") or ""
    house_number = address.get("house_number") or ""
    street_part = f"{street} {house_number}".strip()
    parts = [p for p in (place, street_part, area) if p]
    return {"street": street_part, "area": area, "place": place, "label": ", ".join(parts)}


async def reverse_geocode(latitude: float, longitude: float) -> Optional[dict]:
    """Descripción corta del punto, o None si no se pudo (sin red, sin datos, tiempo agotado)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.get(NOMINATIM_URL, params={
                "format": "jsonv2", "lat": latitude, "lon": longitude, "zoom": 18,
                "addressdetails": 1, "accept-language": "es",
            }, headers={"User-Agent": USER_AGENT})
            res.raise_for_status()
            data = res.json()
    except Exception:
        logger.warning("[Geocoding] No se pudo describir la ubicación (%s, %s).", latitude, longitude, exc_info=True)
        return None
    if not isinstance(data, dict) or "address" not in data:
        return None
    described = describe(data.get("address") or {}, data.get("name"), data.get("category"))
    return described if described["label"] else None
