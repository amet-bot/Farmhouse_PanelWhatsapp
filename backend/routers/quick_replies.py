"""
Respuestas rápidas del Centro WhatsApp.

Usarlas no pide permiso (cualquier agente ve las globales y las de su sucursal). Crearlas o
editarlas pide `quick_replies.manage`: el supervisor de una sucursal solo administra las de SU
sucursal; el admin (o supervisor global) también las globales y las de cualquier sucursal.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models.quick_reply import QuickReply
from models.user import User
from schemas.quick_reply import QuickReplyCreate, QuickReplyUpdate, QuickReplyResponse
from security.access_control import check_target_branch_valid
from security.auth import get_current_user, get_current_authorized_user
from security.permissions import require_permission
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.quick_replies")

router = APIRouter(prefix="/quick-replies", tags=["Respuestas rápidas"])


def _is_global_manager(user: User) -> bool:
    return user.role == "admin" or (user.role == "supervisor" and not user.branch_id)


def _check_can_manage(user: User, branch_id: Optional[int]) -> None:
    """Un supervisor de sucursal solo toca respuestas de su sucursal (ni globales ni ajenas)."""
    if _is_global_manager(user):
        return
    if branch_id is None or branch_id != user.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Solo puedes administrar las respuestas rápidas de tu sucursal.")


def _check_shortcut_free(db: Session, shortcut: str, branch_id: Optional[int], exclude_id: Optional[int] = None) -> None:
    """Un mismo /atajo no puede repetirse entre las globales y las de una sucursal: al escribirlo habría dos."""
    q = db.query(QuickReply).filter(QuickReply.shortcut == shortcut)
    if branch_id is None:
        pass  # global: choca con cualquiera que use ese atajo
    else:
        q = q.filter(or_(QuickReply.branch_id.is_(None), QuickReply.branch_id == branch_id))
    if exclude_id:
        q = q.filter(QuickReply.id != exclude_id)
    if q.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Ya existe una respuesta rápida con el atajo /{shortcut}.")


@router.get("/", response_model=List[QuickReplyResponse])
def list_quick_replies(
    include_inactive: bool = Query(False),
    branch_id: Optional[int] = Query(None, description="Solo para quien administra: filtrar por sucursal"),
    db: Session = Depends(get_db),
    # Sin dispositivo: el selector del chat las carga apenas abre la pantalla, antes de vincular.
    current_user: User = Depends(get_current_user),
):
    q = db.query(QuickReply)
    if current_user.role in ("agent", "supervisor") and current_user.branch_id:
        q = q.filter(or_(QuickReply.branch_id.is_(None), QuickReply.branch_id == current_user.branch_id))
    elif branch_id is not None:
        q = q.filter(or_(QuickReply.branch_id.is_(None), QuickReply.branch_id == branch_id))
    if not include_inactive:
        q = q.filter(QuickReply.active.is_(True))
    return q.order_by(QuickReply.sort_order.asc(), QuickReply.title.asc()).all()


@router.post("/", response_model=QuickReplyResponse, dependencies=[Depends(require_permission("quick_replies.manage"))])
def create_quick_reply(
    data: QuickReplyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    branch_id = data.branch_id
    if not _is_global_manager(current_user):
        branch_id = current_user.branch_id  # el supervisor de sucursal siempre crea para la suya
    if branch_id is not None:
        check_target_branch_valid(db, branch_id)
    _check_shortcut_free(db, data.shortcut, branch_id)

    qr = QuickReply(shortcut=data.shortcut, title=data.title, body=data.body, branch_id=branch_id,
                    active=data.active, sort_order=data.sort_order, created_by_user_id=current_user.id)
    db.add(qr)
    db.flush()
    log_audit_event(db, current_user.id, branch_id, "quick_reply.created", "quick_reply", qr.id, {"shortcut": qr.shortcut})
    db.commit()
    db.refresh(qr)
    return qr


@router.put("/{reply_id}", response_model=QuickReplyResponse, dependencies=[Depends(require_permission("quick_replies.manage"))])
def update_quick_reply(
    reply_id: int,
    data: QuickReplyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    qr = db.query(QuickReply).filter(QuickReply.id == reply_id).first()
    if not qr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta rápida no encontrada.")
    _check_can_manage(current_user, qr.branch_id)

    changes = data.model_dump(exclude_unset=True)
    new_branch = changes.get("branch_id", qr.branch_id)
    if "branch_id" in changes:
        _check_can_manage(current_user, new_branch)
        if new_branch is not None:
            check_target_branch_valid(db, new_branch)
    new_shortcut = changes.get("shortcut", qr.shortcut)
    if new_shortcut != qr.shortcut or new_branch != qr.branch_id:
        _check_shortcut_free(db, new_shortcut, new_branch, exclude_id=qr.id)

    for field, value in changes.items():
        setattr(qr, field, value)
    log_audit_event(db, current_user.id, qr.branch_id, "quick_reply.updated", "quick_reply", qr.id, {"fields": sorted(changes.keys())})
    db.commit()
    db.refresh(qr)
    return qr


@router.delete("/{reply_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("quick_replies.manage"))])
def delete_quick_reply(
    reply_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    qr = db.query(QuickReply).filter(QuickReply.id == reply_id).first()
    if not qr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta rápida no encontrada.")
    _check_can_manage(current_user, qr.branch_id)
    log_audit_event(db, current_user.id, qr.branch_id, "quick_reply.deleted", "quick_reply", qr.id, {"shortcut": qr.shortcut, "title": qr.title})
    db.delete(qr)
    db.commit()
    return None
