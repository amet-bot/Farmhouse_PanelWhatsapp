import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models.device import Device
from models.user import User
from schemas.device import (
    DeviceResponse, DeviceCreate, DeviceUpdate, DeviceWithEnrollCode,
    DeviceEnrollRequest, DeviceEnrollResponse,
)
from security.auth import get_current_user, get_current_authorized_user
from security.access_control import check_target_branch_valid
from security.permissions import require_permission
from services.audit import log_audit_event
from services.device_access import issue_enroll_code, redeem_enroll_code, clear_device_binding, find_device_by_token

logger = logging.getLogger("farmhouse.devices")

router = APIRouter(prefix="/devices", tags=["Dispositivos"])

def generate_device_code() -> str:
    return f"FH-DEVICE-{uuid.uuid4().hex[:6].upper()}"

@router.get("/", response_model=List[DeviceResponse])
def get_devices(
    branch_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    # Deliberadamente solo requiere sesión autenticada (NO get_current_authorized_user): un agente
    # o supervisor en un equipo todavía sin vincular necesita ver la lista para saber qué equipo
    # es el suyo. Listarlos no da acceso: el código FH-DEVICE-… es solo una etiqueta y el token
    # secreto nunca sale de aquí.
    current_user: User = Depends(get_current_user)
):
    query = db.query(Device)

    # Si es agente o supervisor de sucursal, solo consulta los dispositivos de su sucursal
    if current_user.role == "agent" and current_user.branch_id:
        query = query.filter(Device.branch_id == current_user.branch_id)
    elif current_user.role == "supervisor" and current_user.branch_id:
        query = query.filter(Device.branch_id == current_user.branch_id)
    elif branch_id:
        query = query.filter(Device.branch_id == branch_id)

    return query.order_by(Device.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/me", response_model=Optional[DeviceResponse])
def get_my_device(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """El equipo al que está vinculado este navegador (según el token que manda), o null."""
    token = (request.headers.get("X-Device-ID") or "").strip()
    return find_device_by_token(db, token) if token else None


@router.post("/enroll", response_model=DeviceEnrollResponse)
def enroll_device(
    data: DeviceEnrollRequest,
    db: Session = Depends(get_db),
    # Solo sesión: justamente se llama desde un equipo que todavía no está autorizado.
    current_user: User = Depends(get_current_user)
):
    """
    Canjea el código de vinculación (lo generó el admin) por el token secreto de este equipo.
    El código se gasta al usarse; el token se devuelve una sola vez.
    """
    device, secret = redeem_enroll_code(db, data.code, current_user)
    log_audit_event(db, current_user.id, device.branch_id, "device.enrolled", "device", device.id,
                    {"device_id": device.device_id, "name": device.name})
    db.commit()
    db.refresh(device)
    return DeviceEnrollResponse(device_token=secret, device=device)


@router.post("/", response_model=DeviceWithEnrollCode, dependencies=[Depends(require_permission("devices.manage"))])
def register_device(
    device_in: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    # 1. Validar que la sucursal exista en MySQL
    branch = check_target_branch_valid(db, device_in.branch_id)

    # 2. Generar código interno único seguro
    code = generate_device_code()
    while db.query(Device).filter(Device.device_id == code).first():
        code = generate_device_code()

    # 3. Determinar estado inicial
    init_status = "active"
    if device_in.active is False or device_in.status in ["disabled", "revoked"]:
        init_status = "disabled"

    now = datetime.now(timezone.utc)
    device = Device(
        device_id=code,
        name=device_in.name.strip(),
        device_type=device_in.device_type,
        branch_id=branch.id,
        assigned_user_id=device_in.assigned_user_id,
        status=init_status,
        ip_address=device_in.ip_address,
        last_seen=None,
        created_at=now
    )
    # 4. Código de vinculación: se muestra una sola vez al admin, que lo teclea en el equipo.
    enrollment_code = issue_enroll_code(device)
    db.add(device)
    db.flush()
    log_audit_event(db, current_user.id, branch.id, "device.created", "device", device.id,
                    {"device_id": device.device_id, "name": device.name, "status": device.status})
    db.commit()
    db.refresh(device)
    logger.info(f"Dispositivo registrado por Admin ({current_user.username}): '{device.name}' [{device.device_id}] en sucursal '{branch.name}', Estado: {device.status}")
    return DeviceWithEnrollCode(**DeviceResponse.model_validate(device).model_dump(), enrollment_code=enrollment_code)


@router.post("/{device_id_db}/enrollment-code", response_model=DeviceWithEnrollCode, dependencies=[Depends(require_permission("devices.manage"))])
def new_enrollment_code(
    device_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Código de vinculación nuevo para un equipo (por ejemplo, uno registrado antes de este control,
    o una tablet que se reemplazó). Invalida el código anterior; el token ya vinculado sigue
    valiendo hasta que alguien canjee el nuevo código, que lo reemplaza.
    """
    device = db.query(Device).filter(Device.id == device_id_db).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispositivo no encontrado.")
    if device.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Activa el dispositivo antes de generar un código de vinculación.")
    enrollment_code = issue_enroll_code(device)
    log_audit_event(db, current_user.id, device.branch_id, "device.enroll_code_issued", "device", device.id,
                    {"device_id": device.device_id})
    db.commit()
    db.refresh(device)
    return DeviceWithEnrollCode(**DeviceResponse.model_validate(device).model_dump(), enrollment_code=enrollment_code)


@router.put("/{device_id_db}", response_model=DeviceResponse, dependencies=[Depends(require_permission("devices.manage"))])
def update_device(
    device_id_db: int,
    device_in: DeviceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    device = db.query(Device).filter(Device.id == device_id_db).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispositivo no encontrado en base de datos."
        )

    update_data = device_in.model_dump(exclude_unset=True)

    if "branch_id" in update_data and update_data["branch_id"]:
        check_target_branch_valid(db, update_data["branch_id"])

    if "active" in update_data:
        if update_data["active"] is True:
            update_data["status"] = "active"
        elif update_data["active"] is False:
            update_data["status"] = "disabled"
        del update_data["active"]

    for field, value in update_data.items():
        setattr(device, field, value)

    # Revocar desde "Editar" también suelta el token: equivale a /revoke.
    if device.status == "revoked":
        clear_device_binding(device)

    db.commit()
    db.refresh(device)
    logger.info(f"Dispositivo actualizado por Admin ({current_user.username}): ID {device.id} '{device.name}' [{device.device_id}], Estado: {device.status}")
    return device

@router.post("/{device_id_db}/revoke", response_model=DeviceResponse, dependencies=[Depends(require_permission("devices.manage"))])
def revoke_device_access(
    device_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    device = db.query(Device).filter(Device.id == device_id_db).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispositivo no encontrado."
        )
    device.status = "revoked"
    # El token guardado en ese navegador deja de servir en el acto, no solo por el estado.
    clear_device_binding(device)
    log_audit_event(db, current_user.id, device.branch_id, "device.revoked", "device", device.id,
                    {"device_id": device.device_id, "name": device.name})
    db.commit()
    db.refresh(device)
    logger.info(f"Acceso revocado por Admin ({current_user.username}): Dispositivo '{device.name}' [{device.device_id}]")
    return device

@router.post("/{device_id_db}/heartbeat")
def device_heartbeat(
    device_id_db: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    device = db.query(Device).filter(Device.id == device_id_db).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispositivo no encontrado."
        )
    if device.status in ["disabled", "revoked"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este dispositivo no está autorizado para atender conversaciones."
        )

    # Validar que si es agente o supervisor local, el dispositivo pertenezca a su sucursal (Punto 11)
    if current_user.role == "agent":
        if device.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes emitir heartbeat en dispositivos de otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if device.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes emitir heartbeat en dispositivos de otra sucursal."
            )

    device.last_seen = datetime.now(timezone.utc)
    db.commit()
    return {"status": "active", "last_seen": device.last_seen}
