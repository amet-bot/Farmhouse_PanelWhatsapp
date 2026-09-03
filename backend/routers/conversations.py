import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.conversation import Conversation
from models.contact import Contact
from models.branch import Branch
from models.user import User
from models.message import Message
from models.order import Order
from schemas.conversation import ConversationResponse, ConversationCreate, ConversationTransferRequest
from schemas.message import MessageResponse
from security.auth import get_current_authorized_user
from security.access_control import check_conversation_access, check_target_branch_valid
from services.routing_service import RoutingService
from services.websocket_manager import ws_manager
from services.active_cart import expire_stale_carts_for_conversations

logger = logging.getLogger("farmhouse.conversations")

router = APIRouter(prefix="/conversations", tags=["Conversaciones"])

@router.get("/", response_model=List[ConversationResponse])
def get_conversations(
    branch_id: Optional[int] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    skip: int = Query(0, ge=0, description="Número de registros a omitir para paginación"),
    limit: int = Query(50, ge=1, le=100, description="Límite de registros por página"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Listado optimizado y aislado de conversaciones por sucursal (Punto 3).
    """
    query = db.query(Conversation).options(
        joinedload(Conversation.contact),
        joinedload(Conversation.branch),
        joinedload(Conversation.assigned_user)
    ).filter(Conversation.deleted_at.is_(None))

    if current_user.role == "agent":
        if not current_user.branch_id:
            return []
        query = query.filter(Conversation.branch_id == current_user.branch_id)
    elif current_user.role == "supervisor" and current_user.branch_id:
        query = query.filter(Conversation.branch_id == current_user.branch_id)
    elif branch_id:
        query = query.filter(Conversation.branch_id == branch_id)

    if status_filter and status_filter not in ["todas", "all"]:
        if status_filter in ["abiertas", "open"]:
            query = query.filter(Conversation.status.in_(["open", "new", "unassigned"]))
        elif status_filter in ["pendientes", "pending"]:
            query = query.filter(Conversation.status == "pending")
        elif status_filter in ["no-asignadas", "unassigned"]:
            query = query.filter(Conversation.status == "unassigned")
        else:
            query = query.filter(Conversation.status == status_filter)

    if search:
        s_term = f"%{search}%"
        query = query.join(Conversation.contact).filter(
            (Contact.name.ilike(s_term)) | (Contact.phone.ilike(s_term))
        )

    results = query.order_by(Conversation.updated_at.desc()).offset(skip).limit(limit).all()
    # Expiración perezosa de carritos activos abandonados (Punto 16): no hay scheduler/cron en
    # el proyecto, así que se resuelve al leer, sin bloquear el listado con N+1 queries.
    expire_stale_carts_for_conversations(db, [c.id for c in results])
    return results

@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Detalle de una conversación específica con validación de aislamiento.
    """
    check_conversation_access(db, conversation_id, current_user, action="read")

    conv = db.query(Conversation).options(
        joinedload(Conversation.contact),
        joinedload(Conversation.branch),
        joinedload(Conversation.assigned_user),
        joinedload(Conversation.orders)
    ).filter(Conversation.id == conversation_id, Conversation.deleted_at.is_(None)).first()

    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    # Expiración perezosa del carrito activo si quedó abandonado (Punto 16). Se hace ANTES de
    # devolver la respuesta para que un refresh del panel (Test 10) siempre vea el estado real.
    expire_stale_carts_for_conversations(db, [conv.id])
    db.refresh(conv)

    # Cargar últimos 50 mensajes paginados cronológicamente (excluyendo borrados lógicos)
    recent_messages = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None)
    ).order_by(Message.created_at.desc()).limit(50).all()

    conv.messages = sorted(recent_messages, key=lambda m: m.created_at)
    return conv

@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages_paginated(
    conversation_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Carga paginada del historial de mensajes para scroll infinito o carga hacia atrás.
    """
    check_conversation_access(db, conversation_id, current_user, action="read_messages")

    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None)
    ).order_by(Message.created_at.desc()).offset(skip).limit(limit).all()

    return sorted(messages, key=lambda m: m.created_at)

@router.post("/", response_model=ConversationResponse)
def create_conversation(
    conv_in: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    contact = db.query(Contact).filter(Contact.id == conv_in.customer_id, Contact.deleted_at.is_(None)).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contacto no encontrado.")

    # Validación de sucursal para agentes y supervisores locales (Punto 3)
    if current_user.role == "agent":
        if conv_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes crear conversaciones para otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if conv_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para crear conversaciones en otra sucursal."
            )

    branch = check_target_branch_valid(db, conv_in.branch_id)

    # Validar usuario asignado si se especifica
    assigned_user_id = conv_in.assigned_user_id or current_user.id
    if assigned_user_id:
        assigned_u = db.query(User).filter(User.id == assigned_user_id, User.active == True).first()
        if not assigned_u:
            raise HTTPException(status_code=400, detail="El usuario asignado no existe o se encuentra inactivo.")
        if assigned_u.role == "agent" and assigned_u.branch_id != conv_in.branch_id:
            raise HTTPException(status_code=400, detail="El usuario asignado no pertenece a la sucursal de la conversación.")

    now = datetime.now(timezone.utc)
    conv = Conversation(
        customer_id=conv_in.customer_id,
        branch_id=conv_in.branch_id,
        assigned_user_id=assigned_user_id,
        status=conv_in.status or "open",
        created_at=now,
        updated_at=now
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    logger.info(f"Conversación creada ID {conv.id} para cliente ID {contact.id} en sucursal '{branch.name}'")
    return conv

@router.post("/{conversation_id}/take", response_model=ConversationResponse)
def take_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    return RoutingService.take_conversation(db, conversation_id, current_user)

@router.post("/{conversation_id}/transfer", response_model=ConversationResponse)
def transfer_conversation(
    conversation_id: int,
    req: ConversationTransferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    return RoutingService.transfer_conversation(
        db=db,
        conversation_id=conversation_id,
        target_branch_id=req.target_branch_id,
        transferred_by=current_user,
        reason=req.reason
    )

@router.put("/{conversation_id}/status", response_model=ConversationResponse)
def update_conversation_status(
    conversation_id: int,
    new_status: str = Query(..., pattern="^(new|unassigned|open|pending|closed)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    conv = check_conversation_access(db, conversation_id, current_user, action="update_status")

    conv.status = new_status
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    logger.info(f"Estado de conversación ID {conv.id} actualizado a '{new_status}' por usuario ID {current_user.id}")
    return conv

@router.post("/{conversation_id}/toggle-automation")
def toggle_conversation_automation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """Pausa o reanuda las respuestas automáticas del bot en una conversación (Puntos 17 y 18)."""
    conv = check_conversation_access(db, conversation_id, current_user, action="toggle_automation")
    conv.automation_paused = not conv.automation_paused
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    status_str = "pausada" if conv.automation_paused else "reanudada"
    logger.info(f"Automatización {status_str} en conversación ID {conv.id} por {current_user.name}")
    return {"status": "ok", "automation_paused": conv.automation_paused}

@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Borrado lógico seguro de conversaciones (Punto 21).
    Solo para administradores y supervisores de la sucursal.
    """
    if current_user.role not in ["admin", "supervisor"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores y supervisores pueden eliminar conversaciones."
        )

    conv = check_conversation_access(db, conversation_id, current_user, action="delete")
    branch_id = conv.branch_id
    now = datetime.now(timezone.utc)

    # Borrado lógico
    conv.deleted_at = now
    conv.deleted_by = current_user.id
    
    # Marcar también mensajes asociados como borrados lógicos
    db.query(Message).filter(Message.conversation_id == conversation_id).update({
        "deleted_at": now,
        "deleted_by": current_user.id
    })

    db.commit()

    logger.info(f"Conversación ID {conversation_id} borrada lógicamente por {current_user.role.upper()} '{current_user.name}' (@{current_user.username}).")

    try:
        await ws_manager.broadcast_to_branch(branch_id, {
            "type": "conversation_deleted",
            "conversation_id": conversation_id
        })
    except Exception as ws_err:
        logger.error(f"Error difundiendo eliminación de conversación por WebSocket: {ws_err}")

    return {"status": "deleted", "conversation_id": conversation_id}