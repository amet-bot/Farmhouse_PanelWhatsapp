"""
Notificaciones nativas de la app de Android, por Firebase Cloud Messaging (API HTTP v1).

Se autentica con la cuenta de servicio de Firebase (FIREBASE_SERVICE_ACCOUNT_JSON): se firma un
JWT con su clave privada, se cambia por un token de acceso de Google (dura una hora y se reusa) y
con ese token se manda cada mensaje. Sin la cuenta configurada no se manda nada y el resto del
sistema sigue igual.

Cada aviso sale por un canal de la app (importancia alta, ver MainActivity.java): aparece arriba
de la pantalla, suena, vibra y se ve en la pantalla de bloqueo. Hay dos canales con distinto
sonido para que una tarea no se confunda con un mensaje de WhatsApp sin mirar el teléfono:
"avisos" (mensajes de clientes, el de siempre) y "tareas" (tareas nuevas/reasignadas/hechas).
"""
import base64
import json
import logging
import threading
import time
from typing import Optional

import httpx
from jose import jwt

from config import settings

logger = logging.getLogger("farmhouse.fcm")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
CANAL_AVISOS = "avisos"          # el mismo id que crea la app (MainActivity.CANAL_AVISOS)
CANAL_TAREAS = "tareas"          # sonido distinto, para no confundirlo con un mensaje de WhatsApp
ICONO = "ic_stat_farmhouse"      # la hoja blanca de la barra de notificaciones
COLOR = "#2F8F6A"

_lock = threading.Lock()
_token_cache: dict = {"value": None, "expires": 0.0}


def _cuenta_de_servicio() -> Optional[dict]:
    crudo = (settings.FIREBASE_SERVICE_ACCOUNT_JSON or "").strip()
    if not crudo:
        return None
    try:
        if not crudo.startswith("{"):
            crudo = base64.b64decode(crudo).decode("utf-8")
        cuenta = json.loads(crudo)
    except Exception:
        logger.error("[FCM] FIREBASE_SERVICE_ACCOUNT_JSON no es un JSON válido.")
        return None
    if not all(cuenta.get(k) for k in ("project_id", "client_email", "private_key")):
        logger.error("[FCM] A la cuenta de servicio le falta project_id, client_email o private_key.")
        return None
    return cuenta


def is_configured() -> bool:
    return _cuenta_de_servicio() is not None


def _access_token(cuenta: dict) -> str:
    with _lock:
        if _token_cache["value"] and time.time() < _token_cache["expires"] - 60:
            return _token_cache["value"]
        ahora = int(time.time())
        token_uri = cuenta.get("token_uri") or "https://oauth2.googleapis.com/token"
        asercion = jwt.encode(
            {"iss": cuenta["client_email"], "scope": FCM_SCOPE, "aud": token_uri, "iat": ahora, "exp": ahora + 3600},
            cuenta["private_key"], algorithm="RS256",
        )
        res = httpx.post(token_uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": asercion,
        }, timeout=10)
        res.raise_for_status()
        datos = res.json()
        _token_cache["value"] = datos["access_token"]
        _token_cache["expires"] = time.time() + int(datos.get("expires_in", 3600))
        return _token_cache["value"]


def build_message(token: str, title: str, body: str, url: Optional[str] = None, tag: Optional[str] = None, channel: str = CANAL_AVISOS) -> dict:
    """El mensaje de FCM: notificación visible + la ruta a abrir al tocarla (en `data`). `channel`
    decide el sonido (ver MainActivity.java): CANAL_AVISOS (mensajes) o CANAL_TAREAS (tareas)."""
    data = {"url": url or "/hub"}
    if tag:
        data["tag"] = tag
    notificacion = {
        "channel_id": channel, "icon": ICONO, "color": COLOR,
        "default_sound": True, "default_vibrate_timings": True,
        "notification_priority": "PRIORITY_HIGH", "visibility": "PUBLIC",
    }
    if tag:
        notificacion["tag"] = tag   # un aviso nuevo del mismo asunto reemplaza al anterior
    return {
        "message": {
            "token": token,
            "notification": {"title": title[:120], "body": (body or "")[:240]},
            "data": data,
            "android": {"priority": "HIGH", "notification": notificacion},
        }
    }


class TokenInvalido(Exception):
    """El celular desinstaló la app o el token venció: hay que borrarlo."""


def send(token: str, title: str, body: str, url: Optional[str] = None, tag: Optional[str] = None, channel: str = CANAL_AVISOS) -> None:
    """Manda un aviso a un celular. Levanta TokenInvalido si FCM dice que ese token ya no sirve."""
    cuenta = _cuenta_de_servicio()
    if cuenta is None:
        return
    res = httpx.post(
        f"https://fcm.googleapis.com/v1/projects/{cuenta['project_id']}/messages:send",
        headers={"Authorization": f"Bearer {_access_token(cuenta)}"},
        json=build_message(token, title, body, url, tag, channel), timeout=10,
    )
    if res.status_code == 200:
        return
    detalle = res.text[:300]
    if res.status_code == 404 or "UNREGISTERED" in detalle or ("INVALID_ARGUMENT" in detalle and "token" in detalle.lower()):
        raise TokenInvalido(detalle)
    logger.warning(f"[FCM] {res.status_code}: {detalle}")
