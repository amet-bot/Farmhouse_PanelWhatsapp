"""Reglas geográficas del delivery del Menú Digital.

La tarifa se calcula siempre en el servidor usando el pin del cliente y las
coordenadas guardadas de la sucursal. El navegador solo muestra una estimación.
"""
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt


# Cobertura urbana configurada para Ciudad de Panamá. Es deliberadamente más
# estricta que la provincia: puntos de Panamá Oeste/Pacora quedan rechazados.
PANAMA_CITY_BOUNDS = {
    "south": 8.9000,
    "west": -79.6600,
    "north": 9.1300,
    "east": -79.3600,
}


def is_in_panama_city(latitude: float, longitude: float) -> bool:
    return (
        PANAMA_CITY_BOUNDS["south"] <= latitude <= PANAMA_CITY_BOUNDS["north"]
        and PANAMA_CITY_BOUNDS["west"] <= longitude <= PANAMA_CITY_BOUNDS["east"]
    )


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> Decimal:
    """Distancia geodésica Haversine, redondeada a centésimas de km."""
    earth_radius_km = 6371.0088
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    value = 2 * earth_radius_km * asin(sqrt(a))
    return Decimal(str(value)).quantize(Decimal("0.01"))


def fee_for_distance(distance: Decimal) -> Decimal:
    if distance < Decimal("2.00"):
        return Decimal("5.00")
    if distance <= Decimal("5.00"):
        return Decimal("10.00")
    return Decimal("15.00")
