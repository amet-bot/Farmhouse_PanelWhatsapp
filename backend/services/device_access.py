import logging
from datetime import datetime
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models.device import Device
from models.user import User

logger = logging.getLogger("farmhouse.device_access")

def check_device_authorized(db: Session, device_id: str, user: User) -> Device:
    """
    Servicio unificado de validación y autorización de dispositivos.
    Utilizado por: security/auth.py, routers/devices.py, routers/websocket.py

    Reglas:
    1. device_id no puede estar vacío.
    2. device_id debe existir en la base de datos.
    3. device.status debe ser 'active' (no 'disabled' ni 'revoked').
    4. Si user.role == 'agent' y tiene branch_id, device.branch_id debe coincidir con user.branch_id.
    5. Actualiza last_seen en base de datos.
    """
    dev_code = (device_id or "").strip()
    if not dev_code:
        logger.warning(f"Acceso denegado: Usuario ID {user.id} ({user.email}) no proporcionó device_id.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este dispositivo no está autorizado para atender conversaciones. Por favor selecciona o vincula un dispositivo autorizado para tu sucursal."
        )

    device = db.query(Device).filter(Device.device_id == dev_code).first()
    if not device:
        logger.warning(f"Acceso denegado: Dispositivo con código '{dev_code}' no existe en la base de datos.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if user.role == "admin" else status.HTTP_403_FORBIDDEN,
            detail=f"El dispositivo '{dev_code}' no está registrado o autorizado en el sistema."
        )

    if device.status in ["disabled", "revoked"]:
        logger.warning(f"Acceso denegado: Dispositivo '{dev_code}' ({device.name}) está en estado '{device.status}'.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"El dispositivo '{device.name}' ({device.device_id}) ha sido revocado o deshabilitado por el administrador."
        )

    if user.role == "agent" and user.branch_id:
        if device.branch_id != user.branch_id:
            dev_b = device.branch.name if device.branch else f"ID {device.branch_id}"
            user_b = user.branch.name if user.branch else f"ID {user.branch_id}"
            logger.warning(f"Acceso denegado: Dispositivo '{dev_code}' ({dev_b}) no coincide con sucursal del agente ({user_b}).")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Este dispositivo pertenece a la sucursal {dev_b}. Debes usar un equipo asignado a tu sucursal ({user_b})."
            )

    device.last_seen = datetime.utcnow()
    db.commit()
    logger.info(f"Dispositivo autorizado: '{device.device_id}' ({device.name}) para usuario ID {user.id} ({user.email}).")
    return device
