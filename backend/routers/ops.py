import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models.ops import SupplyRequest, Incident, Task
from models.user import User
from schemas.ops import (
    SupplyRequestCreate, SupplyRequestResponse,
    IncidentCreate, IncidentStatusUpdate, IncidentResponse,
    TaskCreate, TaskStatusUpdate, TaskResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

logger = logging.getLogger("farmhouse.ops")

router = APIRouter(prefix="/ops", tags=["Operación de Sucursal"])


def _visible_branch_filter(current_user: User, branch_id: Optional[int]):
    """Mismo criterio que el resto de inventario: admin/supervisor global eligen o ven todo, el resto queda en la suya."""
    if current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None):
        return branch_id
    if current_user.branch_id is None:
        # Sin sucursal (dato inválido) no significa "todas": falla cerrado.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu usuario no tiene una sucursal asignada.")
    return current_user.branch_id


def _require_own_branch_or_admin(current_user: User, branch_id: int, detail: str):
    is_global = current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)
    if not is_global and current_user.branch_id != branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# ==========================================================================
# Solicitudes de insumos
# ==========================================================================
@router.post("/requests", response_model=SupplyRequestResponse, status_code=status.HTTP_201_CREATED)
def create_supply_request(
    request_in: SupplyRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _require_own_branch_or_admin(current_user, request_in.branch_id, "No tienes permiso para pedir insumos en otra sucursal.")
    check_target_branch_valid(db, request_in.branch_id)

    req = SupplyRequest(
        branch_id=request_in.branch_id,
        requested_by_user_id=current_user.id,
        item_name=request_in.item_name,
        quantity_hint=request_in.quantity_hint,
        notes=request_in.notes,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    logger.info(f"Solicitud #{req.id} ({req.item_name}) en sucursal {req.branch_id} por {current_user.name}")
    return SupplyRequestResponse(
        id=req.id, branch_id=req.branch_id, branch_name=req.branch.name,
        requested_by_user_id=req.requested_by_user_id, requested_by_name=req.requested_by_user.name,
        item_name=req.item_name, quantity_hint=req.quantity_hint, notes=req.notes,
        status=req.status, created_at=req.created_at,
        resolved_by_user_id=req.resolved_by_user_id, resolved_at=req.resolved_at,
    )


@router.get("/requests", response_model=List[SupplyRequestResponse])
def list_supply_requests(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(SupplyRequest)
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(SupplyRequest.branch_id == efectiva)
    if status_filter:
        query = query.filter(SupplyRequest.status == status_filter)
    reqs = query.order_by(SupplyRequest.created_at.desc()).offset(offset).limit(limit).all()
    return [
        SupplyRequestResponse(
            id=r.id, branch_id=r.branch_id, branch_name=r.branch.name,
            requested_by_user_id=r.requested_by_user_id, requested_by_name=r.requested_by_user.name,
            item_name=r.item_name, quantity_hint=r.quantity_hint, notes=r.notes,
            status=r.status, created_at=r.created_at,
            resolved_by_user_id=r.resolved_by_user_id, resolved_at=r.resolved_at,
        ) for r in reqs
    ]


@router.post("/requests/{request_id}/status", response_model=SupplyRequestResponse)
def update_supply_request_status(
    request_id: int,
    new_status: str = Query(..., alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    req = db.query(SupplyRequest).filter(SupplyRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada.")
    _require_own_branch_or_admin(current_user, req.branch_id, "No tienes permiso para modificar esta solicitud.")
    if new_status not in SupplyRequest.STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido.")

    req.status = new_status
    if new_status in ("fulfilled", "cancelled"):
        req.resolved_by_user_id = current_user.id
        req.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    return SupplyRequestResponse(
        id=req.id, branch_id=req.branch_id, branch_name=req.branch.name,
        requested_by_user_id=req.requested_by_user_id, requested_by_name=req.requested_by_user.name,
        item_name=req.item_name, quantity_hint=req.quantity_hint, notes=req.notes,
        status=req.status, created_at=req.created_at,
        resolved_by_user_id=req.resolved_by_user_id, resolved_at=req.resolved_at,
    )


# ==========================================================================
# Incidencias
# ==========================================================================
@router.post("/incidents", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
def create_incident(
    incident_in: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _require_own_branch_or_admin(current_user, incident_in.branch_id, "No tienes permiso para reportar incidencias en otra sucursal.")
    check_target_branch_valid(db, incident_in.branch_id)
    if incident_in.severity not in Incident.SEVERITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Severidad no válida.")

    incident = Incident(
        branch_id=incident_in.branch_id,
        reported_by_user_id=current_user.id,
        title=incident_in.title,
        description=incident_in.description,
        severity=incident_in.severity,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    logger.info(f"Incidencia #{incident.id} ({incident.severity}) en sucursal {incident.branch_id} por {current_user.name}")
    return IncidentResponse(
        id=incident.id, branch_id=incident.branch_id, branch_name=incident.branch.name,
        reported_by_user_id=incident.reported_by_user_id, reported_by_name=incident.reported_by_user.name,
        title=incident.title, description=incident.description, severity=incident.severity,
        status=incident.status, created_at=incident.created_at,
        resolved_by_user_id=incident.resolved_by_user_id, resolved_at=incident.resolved_at,
        resolution_notes=incident.resolution_notes,
    )


@router.get("/incidents", response_model=List[IncidentResponse])
def list_incidents(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(Incident)
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(Incident.branch_id == efectiva)
    if status_filter:
        query = query.filter(Incident.status == status_filter)
    incidents = query.order_by(Incident.created_at.desc()).offset(offset).limit(limit).all()
    return [
        IncidentResponse(
            id=i.id, branch_id=i.branch_id, branch_name=i.branch.name,
            reported_by_user_id=i.reported_by_user_id, reported_by_name=i.reported_by_user.name,
            title=i.title, description=i.description, severity=i.severity,
            status=i.status, created_at=i.created_at,
            resolved_by_user_id=i.resolved_by_user_id, resolved_at=i.resolved_at,
            resolution_notes=i.resolution_notes,
        ) for i in incidents
    ]


@router.post("/incidents/{incident_id}/status", response_model=IncidentResponse)
def update_incident_status(
    incident_id: int,
    update: IncidentStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incidencia no encontrada.")
    _require_own_branch_or_admin(current_user, incident.branch_id, "No tienes permiso para modificar esta incidencia.")
    if update.status not in Incident.STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido.")

    incident.status = update.status
    if update.resolution_notes:
        incident.resolution_notes = update.resolution_notes
    if update.status == "resuelta":
        incident.resolved_by_user_id = current_user.id
        incident.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)
    return IncidentResponse(
        id=incident.id, branch_id=incident.branch_id, branch_name=incident.branch.name,
        reported_by_user_id=incident.reported_by_user_id, reported_by_name=incident.reported_by_user.name,
        title=incident.title, description=incident.description, severity=incident.severity,
        status=incident.status, created_at=incident.created_at,
        resolved_by_user_id=incident.resolved_by_user_id, resolved_at=incident.resolved_at,
        resolution_notes=incident.resolution_notes,
    )


# ==========================================================================
# Tareas
# ==========================================================================
@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    task_in: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _require_own_branch_or_admin(current_user, task_in.branch_id, "No tienes permiso para crear tareas en otra sucursal.")
    check_target_branch_valid(db, task_in.branch_id)

    if task_in.assigned_to_user_id is not None:
        assignee = db.query(User).filter(User.id == task_in.assigned_to_user_id).first()
        if not assignee:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El usuario asignado no existe.")

    task = Task(
        branch_id=task_in.branch_id,
        created_by_user_id=current_user.id,
        assigned_to_user_id=task_in.assigned_to_user_id,
        title=task_in.title,
        description=task_in.description,
        due_date=task_in.due_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(f"Tarea #{task.id} en sucursal {task.branch_id} creada por {current_user.name}")
    return TaskResponse(
        id=task.id, branch_id=task.branch_id, branch_name=task.branch.name,
        created_by_user_id=task.created_by_user_id, created_by_name=task.created_by_user.name,
        assigned_to_user_id=task.assigned_to_user_id,
        assigned_to_name=task.assigned_to_user.name if task.assigned_to_user else None,
        title=task.title, description=task.description, status=task.status,
        due_date=task.due_date, created_at=task.created_at, completed_at=task.completed_at,
    )


@router.get("/tasks", response_model=List[TaskResponse])
def list_tasks(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    assigned_to_user_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(Task)
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(Task.branch_id == efectiva)
    if status_filter:
        query = query.filter(Task.status == status_filter)
    if assigned_to_user_id is not None:
        query = query.filter(Task.assigned_to_user_id == assigned_to_user_id)
    tasks = query.order_by(Task.created_at.desc()).offset(offset).limit(limit).all()
    return [
        TaskResponse(
            id=t.id, branch_id=t.branch_id, branch_name=t.branch.name,
            created_by_user_id=t.created_by_user_id, created_by_name=t.created_by_user.name,
            assigned_to_user_id=t.assigned_to_user_id,
            assigned_to_name=t.assigned_to_user.name if t.assigned_to_user else None,
            title=t.title, description=t.description, status=t.status,
            due_date=t.due_date, created_at=t.created_at, completed_at=t.completed_at,
        ) for t in tasks
    ]


@router.post("/tasks/{task_id}/status", response_model=TaskResponse)
def update_task_status(
    task_id: int,
    update: TaskStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    _require_own_branch_or_admin(current_user, task.branch_id, "No tienes permiso para modificar esta tarea.")
    if update.status not in Task.STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido.")

    task.status = update.status
    task.completed_at = datetime.now(timezone.utc) if update.status == "hecha" else None
    db.commit()
    db.refresh(task)
    return TaskResponse(
        id=task.id, branch_id=task.branch_id, branch_name=task.branch.name,
        created_by_user_id=task.created_by_user_id, created_by_name=task.created_by_user.name,
        assigned_to_user_id=task.assigned_to_user_id,
        assigned_to_name=task.assigned_to_user.name if task.assigned_to_user else None,
        title=task.title, description=task.description, status=task.status,
        due_date=task.due_date, created_at=task.created_at, completed_at=task.completed_at,
    )
