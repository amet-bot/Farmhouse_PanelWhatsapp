"""
Operación de sucursal: solicitudes de insumos, incidencias y tareas, más el resumen del Centro
de operación (GET /ops/overview) que junta lo pendiente de todas las sucursales para quien
las maneja desde una sola pantalla (encargado de logística, gerencia).

Reglas de acceso, iguales al resto del sistema: admin y supervisor sin sucursal ("global")
ven y actúan sobre cualquier sucursal; el resto solo sobre la suya. Aprobar o dar por
entregada una solicitud es de encargados (permiso purchasing.approve: supervisor y admin).
Todo cambio deja rastro en la auditoría y avisa por push a quien corresponde.
"""
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.branch import Branch
from models.conversation import Conversation
from models.inventory_item import InventoryItem
from models.ops import SupplyRequest, Incident, Task, TaskPhoto, RecurringTaskTemplate
from models.prep import PrepTemplate, PrepCheck
from models.shipment import ExpectedShipment
from models.stock_count import StockCount
from models.transfer import Transfer
from models.user import User
from schemas.ops import (
    SupplyRequestCreate, SupplyRequestResponse,
    IncidentCreate, IncidentStatusUpdate, IncidentAssign, IncidentResponse,
    TaskCreate, TaskUpdate, TaskStatusUpdate, TaskResponse,
    RecurringTaskCreate, RecurringTaskUpdate, RecurringTaskResponse,
    TeamMember, BranchOverview, OpsOverviewResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid
from security.permissions import has_permission
from services.audit import log_audit_event
from services.branch_hours import PANAMA_TZ
from services.push_service import notify_branch_staff, notify_users
from services import fcm_service

logger = logging.getLogger("farmhouse.ops")

router = APIRouter(prefix="/ops", tags=["Operación de Sucursal"])

OPS_URL = "/gestion"
# Donde ve sus tareas el equipo de la sucursal (quien no entra al Centro de operación).
TASKS_URL = "/tareas"


def _is_global(current_user: User) -> bool:
    return current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)


def _visible_branch_filter(current_user: User, branch_id: Optional[int]):
    """Mismo criterio que el resto de inventario: admin/supervisor global eligen o ven todo, el resto queda en la suya."""
    if _is_global(current_user):
        return branch_id
    if current_user.branch_id is None:
        # Sin sucursal (dato inválido) no significa "todas": falla cerrado.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu usuario no tiene una sucursal asignada.")
    return current_user.branch_id


def _require_own_branch_or_admin(current_user: User, branch_id: int, detail: str):
    if not _is_global(current_user) and current_user.branch_id != branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_manager(current_user: User, detail: str):
    if not has_permission(current_user, "purchasing.approve"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _assignee_or_400(db: Session, user_id: Optional[int], branch_id: int) -> Optional[User]:
    """La persona asignada tiene que existir, estar activa y ser de esa sucursal (o global)."""
    if user_id is None:
        return None
    assignee = db.query(User).filter(User.id == user_id).first()
    if not assignee or not assignee.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El usuario asignado no existe o está inactivo.")
    if assignee.branch_id is not None and assignee.branch_id != branch_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Esa persona es de otra sucursal.")
    return assignee


def _notify_safely(fn, *args, **kwargs) -> None:
    """Un aviso que falla nunca tumba la operación que lo originó."""
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.warning("[Ops] No se pudo mandar el aviso push.", exc_info=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    """Las columnas DATETIME de MySQL vuelven sin zona; para comparar se usa UTC naive."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


# ---- serializadores ----------------------------------------------------------

def _request_out(r: SupplyRequest) -> SupplyRequestResponse:
    return SupplyRequestResponse(
        id=r.id, branch_id=r.branch_id, branch_name=r.branch.name,
        requested_by_user_id=r.requested_by_user_id, requested_by_name=r.requested_by_user.name,
        item_name=r.item_name, quantity_hint=r.quantity_hint, notes=r.notes,
        inventory_item_id=r.inventory_item_id, quantity=r.quantity,
        status=r.status, created_at=r.created_at,
        approved_by_user_id=r.approved_by_user_id,
        approved_by_name=r.approved_by_user.name if r.approved_by_user else None,
        approved_at=r.approved_at,
        resolved_by_user_id=r.resolved_by_user_id,
        resolved_by_name=r.resolved_by_user.name if r.resolved_by_user else None,
        resolved_at=r.resolved_at,
    )


def _incident_out(i: Incident) -> IncidentResponse:
    hours_open = None
    if i.status != "resuelta" and i.created_at:
        hours_open = round((datetime.utcnow() - _naive(i.created_at)).total_seconds() / 3600, 1)
    return IncidentResponse(
        id=i.id, branch_id=i.branch_id, branch_name=i.branch.name,
        reported_by_user_id=i.reported_by_user_id, reported_by_name=i.reported_by_user.name,
        assigned_to_user_id=i.assigned_to_user_id,
        assigned_to_name=i.assigned_to_user.name if i.assigned_to_user else None,
        title=i.title, description=i.description, severity=i.severity,
        status=i.status, created_at=i.created_at,
        resolved_by_user_id=i.resolved_by_user_id,
        resolved_by_name=i.resolved_by_user.name if i.resolved_by_user else None,
        resolved_at=i.resolved_at, resolution_notes=i.resolution_notes,
        hours_open=hours_open,
    )


def _task_link(user: User, task: Task) -> str:
    """El enlace del aviso según lo que esa persona puede abrir: el encargado va al Centro de
    operación; el resto del equipo, a la pantalla de tareas de su sucursal."""
    if has_permission(user, "purchasing.approve"):
        return f"{OPS_URL}?tab=tareas&branch={task.branch_id}"
    return f"{TASKS_URL}?task={task.id}"


def _notify_task(db: Session, user_ids: List[int], title: str, body: str, task: Task, channel: Optional[str] = None) -> None:
    """Manda el aviso de una tarea a esas personas, cada una con el enlace que le sirve. Por el
    canal "tareas" en la app nativa (sonido propio, ver fcm_service.py), para que no se confunda
    con un mensaje de WhatsApp de un cliente sin tener que mirar el teléfono."""
    if not user_ids:
        return
    grupos: dict = {}
    for u in db.query(User).filter(User.id.in_(user_ids), User.active == True).all():  # noqa: E712
        grupos.setdefault(_task_link(u, task), []).append(u.id)
    for url, ids in grupos.items():
        _notify_safely(notify_users, db, ids, title, body, url, tag=f"fh-task-{task.id}", channel=channel or fcm_service.CANAL_TAREAS)


def _branch_team_ids(db: Session, branch_id: int, exclude_user_id: Optional[int] = None) -> List[int]:
    """Todos los usuarios activos de esa sucursal (equipo y encargado local), sin quien crea."""
    q = db.query(User.id).filter(User.branch_id == branch_id, User.active == True)  # noqa: E712
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)
    return [uid for (uid,) in q.all()]


def _task_overdue(t: Task) -> bool:
    due = _naive(t.due_date)
    return bool(due and t.status in ("pendiente", "en_proceso") and due < datetime.utcnow())


def _task_out(t: Task) -> TaskResponse:
    return TaskResponse(
        id=t.id, branch_id=t.branch_id, branch_name=t.branch.name,
        created_by_user_id=t.created_by_user_id, created_by_name=t.created_by_user.name,
        assigned_to_user_id=t.assigned_to_user_id,
        assigned_to_name=t.assigned_to_user.name if t.assigned_to_user else None,
        title=t.title, description=t.description, status=t.status,
        due_date=t.due_date, overdue=_task_overdue(t),
        created_at=t.created_at, completed_at=t.completed_at,
        requires_photo=bool(t.requires_photo),
        photos=[{"id": p.id, "created_at": p.created_at, "uploaded_by_name": p.uploaded_by_user.name if p.uploaded_by_user else None} for p in t.photos],
    )


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
    branch = check_target_branch_valid(db, request_in.branch_id)
    if request_in.inventory_item_id is not None and not db.query(InventoryItem.id).filter(InventoryItem.id == request_in.inventory_item_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El insumo elegido no existe.")

    req = SupplyRequest(
        branch_id=request_in.branch_id,
        requested_by_user_id=current_user.id,
        item_name=request_in.item_name,
        quantity_hint=request_in.quantity_hint,
        inventory_item_id=request_in.inventory_item_id,
        quantity=request_in.quantity,
        notes=request_in.notes,
    )
    db.add(req)
    db.flush()
    log_audit_event(db, current_user.id, req.branch_id, "supply_request.create", "supply_request", req.id, {"item_name": req.item_name})
    db.commit()
    db.refresh(req)
    logger.info(f"Solicitud #{req.id} ({req.item_name}) en sucursal {req.branch_id} por {current_user.name}")
    _notify_safely(
        notify_branch_staff, db, req.branch_id,
        f"Solicitud de insumos · {branch.name}",
        f"{current_user.name} pide {req.item_name}" + (f" ({req.quantity_hint})" if req.quantity_hint else ""),
        f"{OPS_URL}?tab=solicitudes&branch={req.branch_id}", tag=f"fh-request-{req.id}", managers_only=True,
    )
    return _request_out(req)


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
        # "pendientes" = todo lo que todavía no se resolvió (open + approved).
        if status_filter == "pendientes":
            query = query.filter(SupplyRequest.status.in_(["open", "approved"]))
        else:
            query = query.filter(SupplyRequest.status == status_filter)
    reqs = query.order_by(SupplyRequest.created_at.desc()).offset(offset).limit(limit).all()
    return [_request_out(r) for r in reqs]


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
    if req.status in ("fulfilled", "cancelled"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta solicitud ya está cerrada.")

    if new_status in ("approved", "fulfilled"):
        _require_manager(current_user, "Solo un encargado puede aprobar o dar por entregada una solicitud.")
    elif new_status == "cancelled":
        if current_user.id != req.requested_by_user_id and not has_permission(current_user, "purchasing.approve"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo quien la pidió o un encargado puede cancelarla.")
    elif new_status == "open":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Una solicitud no vuelve a 'abierta'.")

    anterior = req.status
    req.status = new_status
    if new_status == "approved":
        req.approved_by_user_id = current_user.id
        req.approved_at = _now()
    if new_status in ("fulfilled", "cancelled"):
        req.resolved_by_user_id = current_user.id
        req.resolved_at = _now()
    log_audit_event(db, current_user.id, req.branch_id, f"supply_request.{new_status}", "supply_request", req.id, {"from": anterior})
    db.commit()
    db.refresh(req)
    if new_status in ("approved", "fulfilled", "cancelled") and req.requested_by_user_id != current_user.id:
        etiqueta = {"approved": "aprobada", "fulfilled": "entregada", "cancelled": "cancelada"}[new_status]
        _notify_safely(
            notify_users, db, [req.requested_by_user_id],
            f"Solicitud {etiqueta}: {req.item_name}", f"{current_user.name} · {req.branch.name}",
            f"{OPS_URL}?tab=solicitudes&branch={req.branch_id}", tag=f"fh-request-{req.id}",
        )
    return _request_out(req)


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
    branch = check_target_branch_valid(db, incident_in.branch_id)
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
    db.flush()
    log_audit_event(db, current_user.id, incident.branch_id, "incident.create", "incident", incident.id, {"severity": incident.severity, "title": incident.title})
    db.commit()
    db.refresh(incident)
    logger.info(f"Incidencia #{incident.id} ({incident.severity}) en sucursal {incident.branch_id} por {current_user.name}")
    # Toda incidencia avisa a los encargados; una grave lo dice en el título para que se note.
    prefijo = "Incidencia GRAVE" if incident.severity == "alta" else "Incidencia"
    _notify_safely(
        notify_branch_staff, db, incident.branch_id,
        f"{prefijo} · {branch.name}", f"{incident.title} · reportada por {current_user.name}",
        f"{OPS_URL}?tab=incidencias&branch={incident.branch_id}", tag=f"fh-incident-{incident.id}", managers_only=True,
    )
    return _incident_out(incident)


@router.get("/incidents", response_model=List[IncidentResponse])
def list_incidents(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
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
        if status_filter == "pendientes":
            query = query.filter(Incident.status != "resuelta")
        else:
            query = query.filter(Incident.status == status_filter)
    if severity:
        query = query.filter(Incident.severity == severity)
    incidents = query.order_by(Incident.created_at.desc()).offset(offset).limit(limit).all()
    return [_incident_out(i) for i in incidents]


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

    anterior = incident.status
    incident.status = update.status
    if update.resolution_notes:
        incident.resolution_notes = update.resolution_notes
    if update.status == "resuelta":
        incident.resolved_by_user_id = current_user.id
        incident.resolved_at = _now()
    else:
        # Reabierta (o vuelta a "en proceso"): ya no está resuelta, así que la fecha y la persona
        # que la cerró dejan de valer. Antes quedaban colgadas y la incidencia parecía cerrada.
        incident.resolved_by_user_id = None
        incident.resolved_at = None
    log_audit_event(db, current_user.id, incident.branch_id, f"incident.{update.status}", "incident", incident.id, {"from": anterior})
    db.commit()
    db.refresh(incident)
    if update.status == "resuelta" and incident.reported_by_user_id != current_user.id:
        _notify_safely(
            notify_users, db, [incident.reported_by_user_id],
            f"Incidencia resuelta: {incident.title}", f"{current_user.name} · {incident.branch.name}",
            f"{OPS_URL}?tab=incidencias&branch={incident.branch_id}", tag=f"fh-incident-{incident.id}",
        )
    return _incident_out(incident)


@router.post("/incidents/{incident_id}/assign", response_model=IncidentResponse)
def assign_incident(
    incident_id: int,
    payload: IncidentAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incidencia no encontrada.")
    _require_own_branch_or_admin(current_user, incident.branch_id, "No tienes permiso para modificar esta incidencia.")
    assignee = _assignee_or_400(db, payload.user_id, incident.branch_id)
    incident.assigned_to_user_id = assignee.id if assignee else None
    if assignee and incident.status == "abierta":
        incident.status = "en_proceso"
    log_audit_event(db, current_user.id, incident.branch_id, "incident.assign", "incident", incident.id, {"user_id": payload.user_id})
    db.commit()
    db.refresh(incident)
    if assignee and assignee.id != current_user.id:
        _notify_safely(
            notify_users, db, [assignee.id],
            f"Te asignaron una incidencia · {incident.branch.name}", incident.title,
            f"{OPS_URL}?tab=incidencias&branch={incident.branch_id}", tag=f"fh-incident-{incident.id}",
        )
    return _incident_out(incident)


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
    assignee = _assignee_or_400(db, task_in.assigned_to_user_id, task_in.branch_id)

    task = Task(
        branch_id=task_in.branch_id,
        created_by_user_id=current_user.id,
        assigned_to_user_id=assignee.id if assignee else None,
        title=task_in.title,
        description=task_in.description,
        due_date=task_in.due_date,
        requires_photo=task_in.requires_photo,
    )
    db.add(task)
    db.flush()
    log_audit_event(db, current_user.id, task.branch_id, "task.create", "task", task.id, {"title": task.title, "assigned_to_user_id": task.assigned_to_user_id})
    db.commit()
    db.refresh(task)
    logger.info(f"Tarea #{task.id} en sucursal {task.branch_id} creada por {current_user.name}")
    cuerpo = task.title + (f" · vence {task.due_date:%d/%m %H:%M}" if task.due_date else "")
    if assignee:
        if assignee.id != current_user.id:
            _notify_task(db, [assignee.id], f"Tarea nueva · {task.branch.name}", cuerpo, task)
    else:
        # Sin asignar es "para la sucursal": le llega a todo el equipo de ahí.
        _notify_task(db, _branch_team_ids(db, task.branch_id, exclude_user_id=current_user.id),
                     f"Tarea para {task.branch.name}", cuerpo, task)
    return _task_out(task)


@router.get("/tasks", response_model=List[TaskResponse])
def list_tasks(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    assigned_to_user_id: Optional[int] = Query(None),
    overdue: Optional[bool] = Query(None),
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
        if status_filter == "pendientes":
            query = query.filter(Task.status.in_(["pendiente", "en_proceso"]))
        else:
            query = query.filter(Task.status == status_filter)
    if assigned_to_user_id is not None:
        query = query.filter(Task.assigned_to_user_id == assigned_to_user_id)
    if overdue:
        query = query.filter(Task.status.in_(["pendiente", "en_proceso"]), Task.due_date < datetime.utcnow())
    # Lo que vence primero arriba; sin fecha, al final; lo más nuevo antes entre iguales.
    tasks = query.order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.created_at.desc()).offset(offset).limit(limit).all()
    return [_task_out(t) for t in tasks]


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    update: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    _require_own_branch_or_admin(current_user, task.branch_id, "No tienes permiso para modificar esta tarea.")
    if task.status in ("hecha", "cancelada"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta tarea ya está cerrada.")

    cambios = {}
    nuevo_asignado = None
    if update.title is not None:
        task.title = update.title
        cambios["title"] = update.title
    if update.description is not None:
        task.description = update.description
    if update.clear_assignee:
        task.assigned_to_user_id = None
        cambios["assigned_to_user_id"] = None
    elif update.assigned_to_user_id is not None and update.assigned_to_user_id != task.assigned_to_user_id:
        nuevo_asignado = _assignee_or_400(db, update.assigned_to_user_id, task.branch_id)
        task.assigned_to_user_id = nuevo_asignado.id
        cambios["assigned_to_user_id"] = nuevo_asignado.id
    if update.requires_photo is not None and bool(update.requires_photo) != bool(task.requires_photo):
        task.requires_photo = bool(update.requires_photo)
        cambios["requires_photo"] = task.requires_photo
    if update.clear_due_date:
        task.due_date = None
        cambios["due_date"] = None
    elif update.due_date is not None:
        task.due_date = update.due_date
        cambios["due_date"] = update.due_date.isoformat()
    log_audit_event(db, current_user.id, task.branch_id, "task.update", "task", task.id, cambios)
    db.commit()
    db.refresh(task)
    if nuevo_asignado and nuevo_asignado.id != current_user.id:
        _notify_task(db, [nuevo_asignado.id], f"Te asignaron una tarea · {task.branch.name}", task.title, task)
    return _task_out(task)


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

    if update.status == "hecha" and task.requires_photo and not task.photos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Esta tarea pide una foto como prueba: súbela para marcarla hecha.")
    anterior = task.status
    task.status = update.status
    task.completed_at = _now() if update.status == "hecha" else None
    log_audit_event(db, current_user.id, task.branch_id, f"task.{update.status}", "task", task.id, {"from": anterior})
    db.commit()
    db.refresh(task)
    if update.status == "hecha" and task.created_by_user_id != current_user.id:
        _notify_task(db, [task.created_by_user_id], f"Tarea hecha: {task.title}", f"{current_user.name} · {task.branch.name}", task)
    return _task_out(task)


@router.post("/tasks/{task_id}/remind", response_model=TaskResponse)
def remind_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Vuelve a avisar de una tarea que sigue pendiente, a la misma gente que ya la vio (quien
    esté asignado, o todo el equipo de la sucursal si sigue sin asignar) — para cuando pasó un
    rato y nadie la tomó. No cambia nada de la tarea, solo repite el aviso push."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    _require_own_branch_or_admin(current_user, task.branch_id, "No tienes permiso para recordar esta tarea.")
    if task.status in ("hecha", "cancelada"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Esta tarea ya está cerrada.")

    if task.assigned_to_user_id:
        destinatarios = [task.assigned_to_user_id] if task.assigned_to_user_id != current_user.id else []
        titulo = f"Recordatorio: tarea pendiente · {task.branch.name}"
    else:
        destinatarios = _branch_team_ids(db, task.branch_id, exclude_user_id=current_user.id)
        titulo = f"Recordatorio: tarea para {task.branch.name}"
    # Recordar suena a urgencia (canal "recordatorios"), distinto del aviso normal de tarea.
    _notify_task(db, destinatarios, titulo, task.title, task, channel=fcm_service.CANAL_RECORDATORIOS)
    log_audit_event(db, current_user.id, task.branch_id, "task.remind", "task", task.id, {})
    db.commit()
    return _task_out(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Borra una tarea de verdad (no solo "cancelada"): para limpiar una creada por error o de
    prueba. A propósito más estricto que el resto de /tasks (ahí alcanza con ser encargado de
    esa sucursal o admin): una tarea es el único registro de "esto se hizo o no se hizo", así
    que borrarla de verdad queda reservado a admin. "Cancelarla" (POST /tasks/{id}/status) sigue
    siendo la forma normal de descartar una tarea sin perder ese historial."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo un administrador puede borrar una tarea.")
    log_audit_event(db, current_user.id, task.branch_id, "task.delete", "task", task.id, {"title": task.title, "status": task.status})
    db.delete(task)
    db.commit()


# ==========================================================================
# Tareas recurrentes
# ==========================================================================
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_times(times: List[str]) -> List[str]:
    limpias = []
    for t in times:
        t = t.strip()
        if not _TIME_RE.match(t):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hora inválida: '{t}' (formato HH:MM, 24 horas).")
        limpias.append(t)
    if not limpias:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agrega al menos una hora.")
    return sorted(set(limpias))


def _recurring_task_out(t: RecurringTaskTemplate) -> RecurringTaskResponse:
    return RecurringTaskResponse(
        id=t.id, branch_id=t.branch_id, branch_name=t.branch.name if t.branch else None,
        created_by_user_id=t.created_by_user_id, created_by_name=t.created_by_user.name,
        title=t.title, description=t.description, frequency=t.frequency,
        times=json.loads(t.times_json), day_of_month=t.day_of_month,
        active=t.active, created_at=t.created_at,
    )


@router.post("/recurring-tasks", response_model=RecurringTaskResponse, status_code=status.HTTP_201_CREATED)
def create_recurring_task(
    template_in: RecurringTaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Crea la regla; no crea ninguna Task todavía — eso lo hace sola
    services/recurring_tasks.py cuando toque la hora/día. `branch_id=None` ("cada local") es
    solo para admin/supervisor global, por ser una política que aplica a todas las sucursales."""
    if template_in.branch_id is None:
        if not _is_global(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo un administrador puede crear una tarea recurrente para todas las sucursales.")
    else:
        _require_own_branch_or_admin(current_user, template_in.branch_id, "No tienes permiso para crear tareas recurrentes en otra sucursal.")
        check_target_branch_valid(db, template_in.branch_id)
    _require_manager(current_user, "Solo un encargado puede crear tareas recurrentes.")

    if template_in.frequency not in RecurringTaskTemplate.FREQUENCIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frecuencia no válida.")
    if template_in.frequency == "monthly" and not template_in.day_of_month:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Indica el día del mes para una tarea mensual.")
    times = _validate_times(template_in.times)

    template = RecurringTaskTemplate(
        branch_id=template_in.branch_id,
        created_by_user_id=current_user.id,
        title=template_in.title,
        description=template_in.description,
        frequency=template_in.frequency,
        times_json=json.dumps(times),
        day_of_month=template_in.day_of_month if template_in.frequency == "monthly" else None,
    )
    db.add(template)
    db.flush()
    log_audit_event(
        db, current_user.id, template.branch_id, "recurring_task.create", "recurring_task_template", template.id,
        {"title": template.title, "frequency": template.frequency, "times": times, "day_of_month": template.day_of_month},
    )
    db.commit()
    db.refresh(template)
    logger.info(f"Tarea recurrente #{template.id} ({template.title}) creada por {current_user.name}.")
    return _recurring_task_out(template)


@router.get("/recurring-tasks", response_model=List[RecurringTaskResponse])
def list_recurring_tasks(
    branch_id: Optional[int] = Query(None),
    active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Lista las reglas visibles: las de su sucursal más las de "cada local" (branch_id NULL),
    que aplican a todos. Un admin/supervisor global puede filtrar por cualquier sucursal."""
    query = db.query(RecurringTaskTemplate)
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter((RecurringTaskTemplate.branch_id == efectiva) | (RecurringTaskTemplate.branch_id.is_(None)))
    if active is not None:
        query = query.filter(RecurringTaskTemplate.active == active)
    templates = query.order_by(RecurringTaskTemplate.created_at.desc()).all()
    return [_recurring_task_out(t) for t in templates]


@router.patch("/recurring-tasks/{template_id}", response_model=RecurringTaskResponse)
def update_recurring_task(
    template_id: int,
    update: RecurringTaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    template = db.query(RecurringTaskTemplate).filter(RecurringTaskTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea recurrente no encontrada.")
    if template.branch_id is None:
        if not _is_global(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo un administrador puede modificar una tarea recurrente de todas las sucursales.")
    else:
        _require_own_branch_or_admin(current_user, template.branch_id, "No tienes permiso para modificar esta tarea recurrente.")
    _require_manager(current_user, "Solo un encargado puede modificar tareas recurrentes.")

    cambios = {}
    if update.title is not None:
        template.title = update.title
        cambios["title"] = update.title
    if update.description is not None:
        template.description = update.description
    if update.times is not None:
        times = _validate_times(update.times)
        template.times_json = json.dumps(times)
        cambios["times"] = times
    if update.day_of_month is not None:
        if template.frequency != "monthly":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El día del mes solo aplica a una tarea mensual.")
        template.day_of_month = update.day_of_month
        cambios["day_of_month"] = update.day_of_month
    if update.active is not None:
        template.active = update.active
        cambios["active"] = update.active
    log_audit_event(db, current_user.id, template.branch_id, "recurring_task.update", "recurring_task_template", template.id, cambios)
    db.commit()
    db.refresh(template)
    return _recurring_task_out(template)


@router.delete("/recurring-tasks/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurring_task(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    template = db.query(RecurringTaskTemplate).filter(RecurringTaskTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea recurrente no encontrada.")
    if template.branch_id is None:
        if not _is_global(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo un administrador puede eliminar una tarea recurrente de todas las sucursales.")
    else:
        _require_own_branch_or_admin(current_user, template.branch_id, "No tienes permiso para eliminar esta tarea recurrente.")
    _require_manager(current_user, "Solo un encargado puede eliminar tareas recurrentes.")
    log_audit_event(db, current_user.id, template.branch_id, "recurring_task.delete", "recurring_task_template", template.id, {"title": template.title})
    db.delete(template)
    db.commit()


TASK_PHOTO_MAX_BYTES = 8 * 1024 * 1024
TASK_PHOTOS_PER_TASK = 4


@router.post("/tasks/{task_id}/photos", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def add_task_photo(
    task_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Foto de prueba de una tarea (la de antes de marcarla hecha). Solo JPG, PNG o WebP,
    verificado por su contenido; hasta TASK_PHOTOS_PER_TASK por tarea."""
    from routers.inventory import _image_type
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    _require_own_branch_or_admin(current_user, task.branch_id, "No tienes permiso sobre esta tarea.")
    if len(task.photos) >= TASK_PHOTOS_PER_TASK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Una tarea admite hasta {TASK_PHOTOS_PER_TASK} fotos.")
    data = await file.read(TASK_PHOTO_MAX_BYTES + 1)
    if len(data) > TASK_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="La foto supera los 8 MB.")
    tipo = _image_type(data)
    if not tipo:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Solo se aceptan fotos (JPG, PNG o WebP).")
    task.photos.append(TaskPhoto(content_type=tipo, size_bytes=len(data), data=data, uploaded_by_user_id=current_user.id))
    log_audit_event(db, current_user.id, task.branch_id, "task.photo_add", "task", task.id, {"size_bytes": len(data)})
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.get("/tasks/{task_id}/photos/{photo_id}")
def get_task_photo(
    task_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
    _require_own_branch_or_admin(current_user, task.branch_id, "No tienes permiso sobre esta tarea.")
    foto = db.query(TaskPhoto).filter(TaskPhoto.id == photo_id, TaskPhoto.task_id == task.id).first()
    if not foto:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto no encontrada.")
    return Response(content=foto.data, media_type=foto.content_type, headers={"Cache-Control": "private, max-age=86400"})


# ==========================================================================
# Centro de operación
# ==========================================================================
@router.get("/team", response_model=List[TeamMember])
def list_team(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """A quién se le puede asignar algo en una sucursal: su gente activa más los usuarios
    globales (admin, supervisor sin sucursal). Cualquier usuario puede consultarlo para su
    propia sucursal; no expone contraseñas ni datos de contacto."""
    efectiva = _visible_branch_filter(current_user, branch_id)
    query = db.query(User).filter(User.active == True)  # noqa: E712
    if efectiva is not None:
        query = query.filter((User.branch_id == efectiva) | (User.branch_id.is_(None)))
    users = query.order_by(User.branch_id.is_(None), User.name).all()
    return [TeamMember(id=u.id, name=u.name, role=u.role, branch_id=u.branch_id) for u in users]


def _branch_overview(db: Session, b: Branch, now_utc: datetime, today) -> BranchOverview:
    def count(q):
        return int(q.count() or 0)

    o = BranchOverview(branch_id=b.id, branch_name=b.name, branch_code=b.code)
    abiertas = db.query(Incident).filter(Incident.branch_id == b.id, Incident.status != "resuelta")
    o.incidents_open = count(abiertas)
    o.incidents_alta = count(abiertas.filter(Incident.severity == "alta"))

    pendientes = db.query(Task).filter(Task.branch_id == b.id, Task.status.in_(["pendiente", "en_proceso"]))
    o.tasks_pending = count(pendientes)
    o.tasks_overdue = count(pendientes.filter(Task.due_date < now_utc))

    o.requests_open = count(db.query(SupplyRequest).filter(SupplyRequest.branch_id == b.id, SupplyRequest.status == "open"))
    o.requests_approved = count(db.query(SupplyRequest).filter(SupplyRequest.branch_id == b.id, SupplyRequest.status == "approved"))

    o.transfers_to_approve = count(db.query(Transfer).filter(Transfer.from_branch_id == b.id, Transfer.status == "requested"))
    o.transfers_to_dispatch = count(db.query(Transfer).filter(Transfer.from_branch_id == b.id, Transfer.status == "approved"))
    o.transfers_to_receive = count(db.query(Transfer).filter(Transfer.to_branch_id == b.id, Transfer.status == "dispatched"))

    esperados = db.query(ExpectedShipment).filter(ExpectedShipment.branch_id == b.id, ExpectedShipment.status == "pendiente")
    o.expected_today = count(esperados.filter(ExpectedShipment.expected_date == today))
    o.expected_overdue = count(esperados.filter(ExpectedShipment.expected_date < today))

    checkpoints = 0
    for t in db.query(PrepTemplate).filter(PrepTemplate.branch_id == b.id, PrepTemplate.active == "Y").all():
        try:
            checkpoints += len(json.loads(t.checkpoints_json or "[]"))
        except (TypeError, ValueError):
            pass
    o.prep_checkpoints_today = checkpoints
    o.prep_filled_today = count(db.query(PrepCheck).filter(PrepCheck.branch_id == b.id, PrepCheck.check_date == today))

    ultimo = db.query(func.max(StockCount.counted_at)).filter(StockCount.branch_id == b.id).scalar()
    if ultimo:
        o.days_since_count = max(0, (now_utc - _naive(ultimo)).days)

    o.unassigned_conversations = count(db.query(Conversation).filter(
        Conversation.branch_id == b.id, Conversation.status == "unassigned", Conversation.deleted_at.is_(None),
    ))

    o.attention = (
        o.incidents_alta * 3 + (o.incidents_open - o.incidents_alta) + o.tasks_overdue * 2 + o.expected_overdue * 2
        + o.transfers_to_receive + o.transfers_to_approve + o.requests_open
        + (2 if o.days_since_count is not None and o.days_since_count > 7 else 0)
    )
    return o


@router.get("/overview", response_model=OpsOverviewResponse)
def ops_overview(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Lo pendiente de cada sucursal en una sola respuesta: incidencias, tareas, solicitudes,
    traslados, cargamentos esperados, prep del día, días desde el último conteo y chats sin
    asignar. Ordenado por `attention` (lo grave y lo vencido primero)."""
    efectiva = _visible_branch_filter(current_user, branch_id)
    query = db.query(Branch).filter(Branch.active == True)  # noqa: E712
    if efectiva is not None:
        query = query.filter(Branch.id == efectiva)
    branches = [b for b in query.order_by(Branch.name).all() if b.code != "CAT" or efectiva is not None]

    now_utc = datetime.utcnow()
    today = datetime.now(PANAMA_TZ).date()
    rows = [_branch_overview(db, b, now_utc, today) for b in branches]
    rows.sort(key=lambda r: (-r.attention, r.branch_name))

    totals = BranchOverview(branch_id=0, branch_name="Todas", branch_code="ALL")
    for r in rows:
        for campo in ("incidents_open", "incidents_alta", "tasks_pending", "tasks_overdue", "requests_open",
                      "requests_approved", "transfers_to_approve", "transfers_to_dispatch", "transfers_to_receive",
                      "expected_today", "expected_overdue", "prep_checkpoints_today", "prep_filled_today",
                      "unassigned_conversations", "attention"):
            setattr(totals, campo, getattr(totals, campo) + getattr(r, campo))
    return OpsOverviewResponse(generated_at=_now(), branches=rows, totals=totals)
