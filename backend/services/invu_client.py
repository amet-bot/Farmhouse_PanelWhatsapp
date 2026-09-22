"""
Cliente de la API de Invu POS.

Invu es el sistema de punto de venta de la casa y ahí viven los proveedores de verdad, con su
RUC, su contacto y su día de entrega. Este módulo es la única puerta por la que este proyecto
habla con esa API: nadie más arma URLs ni maneja el token.

Autenticación (developer.invupos.com): se pide un token con usuario y contraseña y **dura 15
días**. Se cachea en memoria del proceso y se reusa; pedir uno por llamada sería gastar la
cuota de peticiones en algo que ya se tiene. El cache es por proceso a propósito: no vale la
pena una tabla para un dato que se puede volver a pedir en una llamada, y si el proceso se
reinicia se saca uno nuevo y ya.

Límites de la API: 5 por segundo, 60 por minuto, 5.000 por día. Traer el padrón entero de
proveedores son un par de llamadas, así que el techo real no es el volumen sino el ritmo: de
ahí la espera entre páginas y el reintento con espera ante un 429.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx

from config import settings

logger = logging.getLogger("farmhouse.invu")

# La doc dice 15 días; se renueva antes para no quedar con un token que vence a mitad de una
# sincronización.
TOKEN_TTL = timedelta(days=13)

# Tope de la API. Menos páginas, menos llamadas.
PAGE_SIZE = 100

# Freno propio: la API admite 5 por segundo y una sincronización pide páginas en fila.
PAUSE_BETWEEN_PAGES = 0.25

REQUEST_TIMEOUT = 30.0


class InvuError(Exception):
    """Cualquier problema hablando con Invu, ya traducido a algo que se le puede mostrar a alguien."""


class InvuNotConfigured(InvuError):
    def __init__(self):
        super().__init__("La integración con Invu no está configurada en el servidor.")


class _TokenCache:
    """Token compartido por el proceso. Con candado porque el sweep diario y un clic en el
    botón de sincronizar pueden coincidir, y dos hilos pidiendo token a la vez gastan dos
    llamadas para obtener lo mismo."""

    def __init__(self):
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_at: Optional[datetime] = None

    def get(self) -> Optional[str]:
        if self._token and self._expires_at and datetime.now(timezone.utc) < self._expires_at:
            return self._token
        return None

    def set(self, token: str) -> None:
        self._token = token
        self._expires_at = datetime.now(timezone.utc) + TOKEN_TTL

    def clear(self) -> None:
        self._token = None
        self._expires_at = None

    @property
    def lock(self) -> threading.Lock:
        return self._lock


_cache = _TokenCache()


def reset_token_cache() -> None:
    """Para las pruebas y para forzar un login nuevo si las credenciales cambiaron."""
    _cache.clear()


def is_configured() -> bool:
    return settings.is_invu_configured()


def _base_url() -> str:
    return (settings.INVU_API_BASE_URL or "").rstrip("/")


def _authenticate() -> str:
    url = f"{_base_url()}/invuApiPos/userAuth"
    try:
        response = httpx.post(
            url,
            json={
                "username": settings.INVU_API_USERNAME,
                "password": settings.INVU_API_PASSWORD,
                "grant_type": "authorization",
            },
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise InvuError(f"No se pudo contactar a Invu: {e}") from e

    if response.status_code != 200:
        raise InvuError(
            f"Invu rechazó las credenciales (HTTP {response.status_code}). "
            "Revisá el usuario de API en el panel de Invu."
        )

    payload = response.json()
    token = payload.get("authorization")
    if not token:
        raise InvuError("Invu no devolvió un token de autorización.")

    logger.info(f"[Invu] Token obtenido. {payload.get('msg', '')}".strip())
    return token


def get_token(force_refresh: bool = False) -> str:
    if not is_configured():
        raise InvuNotConfigured()

    if not force_refresh:
        cached = _cache.get()
        if cached:
            return cached

    with _cache.lock:
        # Otro hilo pudo haberlo renovado mientras este esperaba el candado.
        if not force_refresh:
            cached = _cache.get()
            if cached:
                return cached
        token = _authenticate()
        _cache.set(token)
        return token


def _get(path_query: str, params: Optional[Dict[str, Any]] = None, _retrying: bool = False) -> Dict[str, Any]:
    """
    Una petición GET a la API. `path_query` es lo que va después de `index.php?r=`, que es como
    Invu enruta (por ejemplo "providers/list").
    """
    token = get_token()
    url = f"{_base_url()}/invuApiPos/index.php"
    query = {"r": path_query, **(params or {})}

    try:
        response = httpx.get(
            url,
            params=query,
            headers={"authorization": token},
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise InvuError(f"No se pudo contactar a Invu: {e}") from e

    # El token venció antes de tiempo (o alguien lo revocó): se saca uno nuevo y se reintenta
    # una sola vez, para no entrar en un ciclo si las credenciales dejaron de servir.
    if response.status_code in (401, 403) and not _retrying:
        logger.info("[Invu] El token dejó de servir; pidiendo uno nuevo.")
        get_token(force_refresh=True)
        return _get(path_query, params, _retrying=True)

    if response.status_code == 429:
        if _retrying:
            raise InvuError("Invu está limitando las peticiones. Probá de nuevo en un minuto.")
        espera = _retry_after_seconds(response)
        logger.warning(f"[Invu] Límite de peticiones alcanzado; esperando {espera}s.")
        time.sleep(espera)
        return _get(path_query, params, _retrying=True)

    if response.status_code != 200:
        raise InvuError(f"Invu respondió HTTP {response.status_code} en '{path_query}'.")

    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise InvuError(str(payload.get("msg") or f"Invu devolvió un error en '{path_query}'."))
    return payload


def _retry_after_seconds(response: httpx.Response) -> float:
    """
    Cuánto esperar tras un 429. La API dice qué ventana se pasó por las cabeceras
    `x-secondlimit-remaining` / `x-minlimit-remaining` / `x-daylimit-remaining`; si se agotó la
    del minuto hay que esperar de verdad, si es la del segundo alcanza con un respiro.
    """
    def agotada(nombre: str) -> bool:
        valor = response.headers.get(nombre)
        return valor is not None and valor.strip() in ("0", "-1")

    if agotada("x-daylimit-remaining"):
        raise InvuError("Se agotó la cuota diaria de peticiones a Invu. Reintentá mañana.")
    if agotada("x-minlimit-remaining"):
        return 60.0
    return 2.0


def iter_providers(updated_after: Optional[datetime] = None) -> Iterator[Dict[str, Any]]:
    """
    Recorre el padrón de proveedores, página por página.

    `updated_after` hace la sincronización incremental: después de la primera carga completa
    solo se piden los que cambiaron, que suelen ser ninguno o unos pocos.
    """
    page = 1
    while True:
        params: Dict[str, Any] = {"page": page, "per_page": PAGE_SIZE}
        if updated_after:
            params["updated_at[after]"] = updated_after.strftime("%Y-%m-%d %H:%M:%S")

        payload = _get("providers/list", params)
        filas: List[Dict[str, Any]] = payload.get("data") or []
        for fila in filas:
            yield fila

        last_page = int(payload.get("last_page") or 0)
        if page >= last_page or not filas:
            return
        page += 1
        time.sleep(PAUSE_BETWEEN_PAGES)
