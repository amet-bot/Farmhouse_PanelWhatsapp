import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.branch import Branch
from models.internal_chat import InternalThread, InternalParticipant, InternalMessage
from models.user import User
from schemas.internal_chat import (
    DirectThreadCreate, InternalMessageCreate, InternalMessageResponse,
    InternalThreadDetail, InternalThreadResponse, InternalUserBrief,
)
from security.auth import get_current_authorized_user
from services.websocket_manager import ws_manager

logger = logging.getLogger("farmhouse.internal")

router = APIRouter(prefix="/internal", tags=["Comunicación Interna"])

PREVIEW_LENGTH = 90


# ==========================================================================
# Ayudantes
# ==========================================================================
def _brief(user: User, online_ids: set) -> InternalUserBrief:
    return InternalUserBrief(
        id=user.id,
        name=user.name,
        role=user.role,
        branch_id=user.branch_id,
        branch_name=(user.branch.name if user.branch else None),
        online=user.id in online_ids,
    )


def ensure_branch_thread(db: Session, user: User) -> Optional[InternalThread]:
    """
    Garantiza que exista el canal del equipo de la sucursal del usuario y que TODOS los
    activos de esa sucursal figuren como participantes.

    Se sincroniza acá, al abrir la bandeja, en vez de crear el canal por migración o al dar
    de alta a alguien: así dar de alta un agente nuevo no obliga a acordarse de meterlo en
    ningún lado, y una sucursal que todavía no usó el sistema no arrastra un hilo vacío.
    Un admin o supervisor global no tiene sucursal propia, así que no tiene canal de equipo.
    """
    if not user.branch_id:
        return None

    thread = db.query(InternalThread).filter(
        InternalThread.kind == "branch",
        InternalThread.branch_id == user.branch_id,
    ).first()

    if not thread:
        thread = InternalThread(kind="branch", branch_id=user.branch_id)
        db.add(thread)
        db.flush()

    member_ids = {
        row.id for row in
        db.query(User.id).filter(User.branch_id == user.branch_id, User.active == True).all()
    }
    existing_ids = {
        row.user_id for row in
        db.query(InternalParticipant.user_id).filter(InternalParticipant.thread_id == thread.id).all()
    }
    for missing in member_ids - existing_ids:
        db.add(InternalParticipant(thread_id=thread.id, user_id=missing))

    db.commit()
    db.refresh(thread)
    return thread


def _participant_or_403(db: Session, thread_id: int, user: User) -> InternalParticipant:
    participant = db.query(InternalParticipant).filter(
        InternalParticipant.thread_id == thread_id,
        InternalParticipant.user_id == user.id,
    ).first()
    if not participant:
        # Mismo mensaje exista o no el hilo: un 404 distinto del 403 delataría qué ids existen.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés acceso a esta conversación.",
        )
    return participant


def _serialize_thread(db: Session, thread: InternalThread, me: User, online_ids: set) -> InternalThreadResponse:
    last = db.query(InternalMessage).filter(
        InternalMessage.thread_id == thread.id
    ).order_by(InternalMessage.created_at.desc(), InternalMessage.id.desc()).first()

    participant = next((p for p in thread.participants if p.user_id == me.id), None)
    unread_q = db.query(func.count(InternalMessage.id)).filter(
        InternalMessage.thread_id == thread.id,
        InternalMessage.sender_user_id != me.id,
    )
    if participant and participant.last_read_at:
        unread_q = unread_q.filter(InternalMessage.created_at > participant.last_read_at)
    unread = unread_q.scalar() or 0

    preview = None
    sender_name = None
    if last:
        preview = last.body if len(last.body) <= PREVIEW_LENGTH else last.body[:PREVIEW_LENGTH].rstrip() + "…"
        sender_name = "Vos" if last.sender_user_id == me.id else last.sender.name.split(" ")[0]

    if thread.kind == "branch":
        members = [p for p in thread.participants]
        return InternalThreadResponse(
            id=thread.id,
            kind="branch",
            title=f"Equipo {thread.branch.name}" if thread.branch else "Equipo",
            subtitle=f"{len(members)} integrantes",
            branch_id=thread.branch_id,
            member_count=len(members),
            last_message_at=thread.last_message_at,
            last_message_preview=preview,
            last_message_sender=sender_name,
            unread_count=unread,
        )

    other = next((p.user for p in thread.participants if p.user_id != me.id), None)
    subtitle = None
    if other:
        subtitle = other.branch.name if other.branch else "Sin sucursal"
        if other.role != "agent":
            subtitle = f"{subtitle} · {other.role.capitalize()}"

    return InternalThreadResponse(
        id=thread.id,
        kind="direct",
        title=other.name if other else "Conversación",
        subtitle=subtitle,
        branch_id=(other.branch_id if other else None),
        counterpart=(_brief(other, online_ids) if other else None),
        last_message_at=thread.last_message_at,
        last_message_preview=preview,
        last_message_sender=sender_name,
        unread_count=unread,
    )


# ==========================================================================
# Directorio
# ==========================================================================
@router.get("/directory", response_model=List[InternalUserBrief])
def get_directory(
    q: str = Query("", max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Todo el personal activo, de cualquier sucursal, menos uno mismo.

    Endpoint propio y no /users/, que le recorta la lista a los agentes dejándoles ver solo
    su propia sucursal: acá el objetivo es justamente que Costa del Este pueda escribirle a
    Clayton. Devuelve lo justo para una fila del directorio, nunca correos ni hashes.
    """
    query = db.query(User).options(joinedload(User.branch)).filter(
        User.active == True,
        User.id != current_user.id,
    )
    q = q.strip()
    if q:
        query = query.filter(User.name.ilike(f"%{q}%"))

    users = query.order_by(User.name.asc()).limit(200).all()
    online_ids = ws_manager.online_user_ids()
    return [_brief(u, online_ids) for u in users]


# ==========================================================================
# Hilos
# ==========================================================================
@router.get("/threads", response_model=List[InternalThreadResponse])
def list_threads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    ensure_branch_thread(db, current_user)

    threads = db.query(InternalThread).join(
        InternalParticipant, InternalParticipant.thread_id == InternalThread.id
    ).filter(
        InternalParticipant.user_id == current_user.id
    ).options(
        joinedload(InternalThread.participants).joinedload(InternalParticipant.user).joinedload(User.branch),
        joinedload(InternalThread.branch),
    ).all()

    online_ids = ws_manager.online_user_ids()
    items = [_serialize_thread(db, t, current_user, online_ids) for t in threads]
    # Primero lo que tiene actividad, y el canal del equipo arriba cuando nadie escribió aún.
    items.sort(key=lambda t: (t.last_message_at is None, -(t.last_message_at.timestamp() if t.last_message_at else 0)))
    return items


@router.post("/threads/direct", response_model=InternalThreadResponse, status_code=status.HTTP_201_CREATED)
def open_direct_thread(
    payload: DirectThreadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Abre (o recupera) el hilo directo con otra persona. Idempotente a propósito."""
    if payload.user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No podés abrir un chat con vos mismo.")

    other = db.query(User).filter(User.id == payload.user_id, User.active == True).first()
    if not other:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esa persona no existe o está inactiva.")

    mine = db.query(InternalParticipant.thread_id).filter(InternalParticipant.user_id == current_user.id).subquery()
    existing = db.query(InternalThread).join(
        InternalParticipant, InternalParticipant.thread_id == InternalThread.id
    ).filter(
        InternalThread.kind == "direct",
        InternalParticipant.user_id == other.id,
        InternalThread.id.in_(db.query(mine.c.thread_id)),
    ).first()

    if not existing:
        existing = InternalThread(kind="direct")
        db.add(existing)
        db.flush()
        db.add(InternalParticipant(thread_id=existing.id, user_id=current_user.id))
        db.add(InternalParticipant(thread_id=existing.id, user_id=other.id))
        db.commit()
        db.refresh(existing)

    return _serialize_thread(db, existing, current_user, ws_manager.online_user_ids())


@router.get("/threads/{thread_id}", response_model=InternalThreadDetail)
def get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _participant_or_403(db, thread_id, current_user)
    thread = db.query(InternalThread).filter(InternalThread.id == thread_id).first()
    online_ids = ws_manager.online_user_ids()
    base = _serialize_thread(db, thread, current_user, online_ids)
    return InternalThreadDetail(
        **base.model_dump(),
        members=[_brief(p.user, online_ids) for p in thread.participants],
    )


# ==========================================================================
# Mensajes
# ==========================================================================
@router.get("/threads/{thread_id}/messages", response_model=List[InternalMessageResponse])
def list_messages(
    thread_id: int,
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    _participant_or_403(db, thread_id, current_user)

    query = db.query(InternalMessage).options(joinedload(InternalMessage.sender)).filter(
        InternalMessage.thread_id == thread_id
    )
    if before_id:
        query = query.filter(InternalMessage.id < before_id)

    # Se piden los más nuevos y se devuelven en orden de lectura (viejo → nuevo).
    rows = query.order_by(InternalMessage.id.desc()).limit(limit).all()
    rows.reverse()
    return [
        InternalMessageResponse(
            id=m.id, thread_id=m.thread_id, sender_user_id=m.sender_user_id,
            sender_name=m.sender.name, body=m.body, created_at=m.created_at,
        )
        for m in rows
    ]


@router.post("/threads/{thread_id}/messages", response_model=InternalMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    thread_id: int,
    payload: InternalMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    participant = _participant_or_403(db, thread_id, current_user)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mensaje está vacío.")

    now = datetime.now(timezone.utc)
    message = InternalMessage(
        thread_id=thread_id,
        sender_user_id=current_user.id,
        body=body,
        created_at=now,
    )
    db.add(message)

    thread = db.query(InternalThread).filter(InternalThread.id == thread_id).first()
    thread.last_message_at = now
    # Quien escribe ya leyó lo suyo: si no, su propio mensaje le contaría como no leído.
    participant.last_read_at = now

    db.commit()
    db.refresh(message)

    response = InternalMessageResponse(
        id=message.id, thread_id=thread_id, sender_user_id=current_user.id,
        sender_name=current_user.name, body=body, created_at=message.created_at,
    )

    # Difusión dirigida: uno por uno a los participantes, no por sala de sucursal. Un hilo
    # directo cruza sucursales, así que la regla de audiencia de notification_audience.py
    # (pensada para eventos de una sucursal) no aplica acá — el destinatario es el hilo.
    event = {
        "type": "internal_message",
        "thread_id": thread_id,
        "thread_kind": thread.kind,
        "message": response.model_dump(mode="json"),
    }
    for p in thread.participants:
        await ws_manager.send_personal_message(event, p.user_id)

    logger.info(f"Mensaje interno #{message.id} en hilo {thread_id} de {current_user.name}")
    return response


@router.post("/threads/{thread_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    participant = _participant_or_403(db, thread_id, current_user)
    participant.last_read_at = datetime.now(timezone.utc)
    db.commit()
    return None
