import hashlib
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models.device import Device
from models.user import User

logger = logging.getLogger("farmhouse.device_access")

LAST_SEEN_WRITE_INTERVAL_SECONDS = 60

# El código de vinculación lo teclea una persona en la tablet: corto, sin letras que se confundan
# (0/O, 1/I) y con un guion al medio para leerlo de un vistazo. Caduca en 24 h o al usarse.
ENROLL_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENROLL_CODE_TTL_HOURS = 24


def hash_token(value: str) -> str:
    """SHA-256 en hex. En la base solo viven hashes: ni el token del equipo ni el código de vinculación."""
    return hashlib.sha256((value or "").strip().encode("utf-8")).hexdigest()


def generate_enroll_code() -> str:
    chars = [secrets.choice(ENROLL_CODE_ALPHABET) for _ in range(8)]
    return "".join(chars[:4]) + "-" + "".join(chars[4:])


def normalize_enroll_code(code: str) -> str:
    """Acepta el código como lo escriba la persona: minúsculas, con o sin guion, con espacios."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def enroll_code_hash(code: str) -> str:
    return hash_token(normalize_enroll_code(code))


def generate_device_secret() -> str:
    """Token que guarda el navegador vinculado y manda en X-Device-ID. Nunca se vuelve a mostrar."""
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def issue_enroll_code(device: Device) -> str:
    """Genera un código de vinculación nuevo (invalida el anterior) y devuelve el texto plano, una sola vez."""
    code = generate_enroll_code()
    device.enroll_code_hash = enroll_code_hash(code)
    device.enroll_expires_at = _now() + timedelta(hours=ENROLL_CODE_TTL_HOURS)
    return code


def clear_device_binding(device: Device) -> None:
    """Deja al equipo sin token ni código: desde ese aparato ya no se puede entrar hasta vincularlo de nuevo."""
    device.secret_hash = None
    device.enroll_code_hash = None
    device.enroll_expires_at = None
    device.enrolled_at = None


def find_device_by_token(db: Session, token: str) -> Optional[Device]:
    tok = (token or "").strip()
    if not tok:
        return None
    return db.query(Device).filter(Device.secret_hash == hash_token(tok)).first()


def redeem_enroll_code(db: Session, code: str, user: User) -> tuple[Device, str]:
    """
    Canjea un código de vinculación por el token secreto del equipo. Un solo uso: al canjearlo
    se borra el código y se reemplaza cualquier token anterior (si el equipo se vuelve a vincular,
    el navegador viejo queda fuera).
    """
    normalized = normalize_enroll_code(code)
    if len(normalized) != 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El código de vinculación tiene 8 letras y números (por ejemplo ABCD-2345).")

    device = db.query(Device).filter(Device.enroll_code_hash == hash_token(normalized)).first()
    if not device or not device.enroll_expires_at or device.enroll_expires_at < _now():
        logger.warning(f"Vinculación rechazada: código inválido o vencido (usuario ID {user.id}, @{user.username}).")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ese código de vinculación no existe o ya venció. Pide uno nuevo al administrador.")

    if device.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"El dispositivo '{device.name}' está {('revocado' if device.status == 'revoked' else 'deshabilitado')}: actívalo antes de vincularlo.")

    # Un agente o supervisor de sucursal solo puede vincular equipos de SU sucursal; el admin y el
    # supervisor global pueden vincular cualquiera (ellos instalan las tablets).
    if user.role in ("agent", "supervisor") and user.branch_id and device.branch_id != user.branch_id:
        dev_b = device.branch.name if device.branch else f"ID {device.branch_id}"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Ese dispositivo es de la sucursal {dev_b}; solo puedes vincular equipos de tu sucursal.")

    secret = generate_device_secret()
    device.secret_hash = hash_token(secret)
    device.enroll_code_hash = None
    device.enroll_expires_at = None
    device.enrolled_at = _now()
    device.last_seen = device.enrolled_at
    logger.info(f"Dispositivo vinculado: '{device.device_id}' ({device.name}) por usuario ID {user.id} (@{user.username}).")
    return device, secret


def check_device_authorized(db: Session, device_token: str, user: User) -> Device:
    """
    Servicio unificado de validación y autorización de dispositivos.
    Utilizado por: security/auth.py, routers/websocket.py

    `device_token` es el token secreto que recibió el equipo al vincularse (header X-Device-ID /
    query device_id del WebSocket). El código público FH-DEVICE-… NO autoriza: antes bastaba con
    conocerlo (y la API lo listaba), así que el control era solo aparente.

    Reglas:
    1. El token no puede estar vacío.
    2. Debe corresponder a un dispositivo vinculado.
    3. device.status debe ser 'active' (no 'disabled' ni 'revoked').
    4. Si user.role == 'agent' y tiene branch_id, device.branch_id debe coincidir con user.branch_id.
    5. Actualiza last_seen en base de datos (a lo sumo una vez por minuto).
    """
    tok = (device_token or "").strip()
    if not tok:
        logger.warning(f"Acceso denegado: Usuario ID {user.id} ({user.email}) no proporcionó dispositivo.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este dispositivo no está autorizado para atender conversaciones. Pide al administrador un código de vinculación y escríbelo en «Vincular este equipo»."
        )

    device = find_device_by_token(db, tok)
    if not device:
        logger.warning(f"Acceso denegado: token de dispositivo desconocido (usuario ID {user.id}, @{user.username}).")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este dispositivo ya no está vinculado o autorizado en el sistema. Vuelve a vincularlo con un código nuevo del administrador."
        )

    if device.status in ["disabled", "revoked"]:
        logger.warning(f"Acceso denegado: Dispositivo '{device.device_id}' ({device.name}) está en estado '{device.status}'.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"El dispositivo '{device.name}' ({device.device_id}) ha sido revocado o deshabilitado por el administrador."
        )

    if user.role == "agent" and user.branch_id:
        if device.branch_id != user.branch_id:
            dev_b = device.branch.name if device.branch else f"ID {device.branch_id}"
            user_b = user.branch.name if user.branch else f"ID {user.branch_id}"
            logger.warning(f"Acceso denegado: Dispositivo '{device.device_id}' ({dev_b}) no coincide con sucursal del agente ({user_b}).")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Este dispositivo pertenece a la sucursal {dev_b}. Debes usar un equipo asignado a tu sucursal ({user_b})."
            )

    # Esto corre en CADA petición autenticada de agentes y supervisores: escribir y hacer commit
    # de last_seen cada vez era un UPDATE por request (además del heartbeat de 30 s). Con
    # actualizarlo una vez por minuto alcanza para saber si el equipo está en uso.
    now = _now()
    last_seen = device.last_seen.replace(tzinfo=None) if device.last_seen else None
    if last_seen is None or (now - last_seen).total_seconds() > LAST_SEEN_WRITE_INTERVAL_SECONDS:
        device.last_seen = now
        db.commit()
    logger.debug(f"Dispositivo autorizado: '{device.device_id}' ({device.name}) para usuario ID {user.id} ({user.email}).")
    return device


def touch_admin_device(db: Session, device_token: str) -> Optional[Device]:
    """
    Los administradores entran desde cualquier equipo; si además mandan el token de uno vinculado,
    se registra que ese equipo está en uso. No bloquea nada.
    """
    device = find_device_by_token(db, device_token)
    if device and device.status == "active":
        device.last_seen = _now()
        db.commit()
        return device
    return None
