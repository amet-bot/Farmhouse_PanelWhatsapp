import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models.device import Device
from models.branch import Branch
from models.user import User
from schemas.device import DeviceResponse, DeviceCreate, DeviceUpdate
from security.auth import get_current_user, get_current_authorized_user, require_role
from security.access_control import check_target_branch_valid
from services.device_access import check_device_authorized

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
    # Deliberadamente solo requiere sesión autenticada (NO get_current_authorized_user):
    # este es el endpoint que el frontend usa para descubrir/auto-vincular un dispositivo
    # autorizado. Si exigiera un dispositivo ya autorizado para poder listarlos, un agente
    # o supervisor sin dispositivo vinculado (o con uno viejo/revocado en localStorage)
    # quedaría bloqueado para siempre: no podría ver la lista de dispositivos válidos de
    # su sucursal ni auto-vincularse a ninguno, aunque el admin ya los haya registrado.
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

@router.get("/verify/{device_code}", response_model=DeviceResponse)
def verify_device(
    device_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Verifica y autoriza el dispositivo utilizando el servicio centralizado check_device_authorized.
    """
    return check_device_authorized(db, device_code, current_user)

@router.post("/", response_model=DeviceResponse, dependencies=[Depends(require_role(["admin"]))])
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
        last_seen=now,
        created_at=now
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    logger.info(f"Dispositivo autorizado creado por Admin ({current_user.username}): '{device.name}' [{device.device_id}] en sucursal '{branch.name}', Estado: {device.status}")
    return device

@router.put("/{device_id_db}", response_model=DeviceResponse, dependencies=[Depends(require_role(["admin"]))])
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

    db.commit()
    db.refresh(device)
    logger.info(f"Dispositivo actualizado por Admin ({current_user.username}): ID {device.id} '{device.name}' [{device.device_id}], Estado: {device.status}")
    return device

@router.post("/{device_id_db}/revoke", response_model=DeviceResponse, dependencies=[Depends(require_role(["admin"]))])
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
    if current_user.role == "agent" and device.branch_id != current_user.branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No puedes emitir heartbeat en dispositivos de otra sucursal."
        )

    device.last_seen = datetime.now(timezone.utc)
    db.commit()
    return {"status": "active", "last_seen": device.last_seen}
