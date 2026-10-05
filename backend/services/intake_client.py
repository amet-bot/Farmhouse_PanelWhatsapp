"""
Cliente del servicio público del formulario del colaborador (carpeta public_intake/, otro proyecto).

Este sistema es quien llama (nunca al revés): registra las invitaciones, va a buscar los sobres
cifrados y los borra del servicio al importarlos. El servicio público no tiene ninguna clave ni acceso
a este sistema, y este sistema no abre ningún endpoint nuevo hacia internet para él.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from config import settings

logger = logging.getLogger("farmhouse.intake_client")

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class IntakeServiceError(Exception):
    """El servicio público no está configurado o no respondió bien."""


def is_configured() -> bool:
    return bool((settings.INTAKE_SERVICE_URL or "").strip() and len((settings.INTAKE_ADMIN_KEY or "").strip()) >= 32)


def public_base_url() -> str:
    return (settings.INTAKE_PUBLIC_URL or settings.INTAKE_SERVICE_URL or "").strip().rstrip("/")


def invite_link(token: str) -> str:
    # El token va en el #fragmento: el navegador no lo manda a ningún servidor ni queda en registros.
    return f"{public_base_url()}/colaborador#t={token}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _request(method: str, path: str, **kwargs):
    if not is_configured():
        raise IntakeServiceError("El formulario público no está configurado en el servidor.")
    base = settings.INTAKE_SERVICE_URL.strip().rstrip("/")
    if settings.ENVIRONMENT == "production" and not base.lower().startswith("https://"):
        raise IntakeServiceError("INTAKE_SERVICE_URL debe ser https:// en producción.")
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            res = client.request(method, base + path, headers={"Authorization": f"Bearer {settings.INTAKE_ADMIN_KEY.strip()}"}, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("[Intake] No se pudo contactar el servicio: %s", type(exc).__name__)
        raise IntakeServiceError("No se pudo contactar el formulario público.") from exc
    if res.status_code >= 400:
        logger.warning("[Intake] %s %s -> %s", method, path, res.status_code)
        raise IntakeServiceError(f"El formulario público respondió con un error ({res.status_code}).")
    return res


def register_invite(token_hash: str, label: Optional[str], expires_at: datetime) -> None:
    """Le avisa al servicio de una invitación nueva: solo viaja el HASH del token, nunca el token."""
    exp = expires_at.replace(tzinfo=timezone.utc).isoformat()
    _request("POST", "/admin/invites", json={"token_hash": token_hash, "label": label, "expires_at": exp})


def revoke_invite(token_hash: str) -> None:
    _request("DELETE", f"/admin/invites/{token_hash}")


def fetch_submissions(limit: int = 50) -> List[dict]:
    return _request("GET", "/admin/submissions", params={"limit": limit}).json()


def ack_submission(remote_id: int) -> None:
    _request("DELETE", f"/admin/submissions/{remote_id}")


def get_open_form() -> dict:
    return _request("GET", "/admin/open-form").json()


def set_open_form(enabled: bool) -> dict:
    return _request("PUT", "/admin/open-form", json={"enabled": bool(enabled)}).json()


def open_form_link() -> str:
    return f"{public_base_url()}/colaborador"
