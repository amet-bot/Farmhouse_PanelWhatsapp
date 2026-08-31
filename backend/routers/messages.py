import logging
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from database import get_db
from models.message import Message
from models.conversation import Conversation
from models.user import User
from schemas.message import MessageResponse, MessageCreate
from security.auth import get_current_authorized_user
from security.access_control import check_conversation_access
from services.whatsapp_service import get_whatsapp_service
from services.websocket_manager import ws_manager

logger = logging.getLogger("farmhouse.messages")

router = APIRouter(prefix="/messages", tags=["Mensajes"])

@router.get("/conversation/{conversation_id}", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    check_conversation_access(db, conversation_id, current_user, action="read_messages")

    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None)
    ).order_by(Message.created_at.desc()).offset(skip).limit(limit).all()

    return sorted(messages, key=lambda m: m.created_at)

@router.post("/", response_model=MessageResponse)
async def send_message(
    msg_in: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Envía un mensaje saliente a través de WhatsApp Cloud API (Punto 10).
    Registra el estado real ('sent' o 'failed') y notifica por WebSocket.
    """
    conv = check_conversation_access(db, msg_in.conversation_id, current_user, action="send_message")

    is_internal = msg_in.is_internal or False
    wamid = None
    msg_status = "sent"
    error_detail = None

    # 1. Enviar por WhatsApp solo si NO es una nota interna
    if not is_internal:
        contact = conv.contact
        if not contact or not contact.phone:
            msg_status = "failed"
            error_detail = "El contacto no tiene un número de teléfono registrado."
        else:
            wa_service = get_whatsapp_service()
            try:
                send_res = await wa_service.send_text_message(contact.phone, msg_in.content)
                if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
                    wamid = send_res["messages"][0].get("id")
                msg_status = "sent"
                logger.info(f"Mensaje de agente enviado exitosamente a WhatsApp. Conv ID {conv.id}, WAMID {wamid}")
            except Exception as e:
                msg_status = "failed"
                error_detail = str(e)
                logger.error(f"Fallo al enviar mensaje a WhatsApp para Conv ID {conv.id} ({contact.phone}): {e}")

    # 2. Guardar el mensaje en la base de datos con su estado real
    now = datetime.now(timezone.utc)
    msg = Message(
        conversation_id=msg_in.conversation_id,
        direction="outgoing",
        sender_type="agent",
        sender_id=current_user.id,
        content=msg_in.content,
        is_internal=is_internal,
        whatsapp_message_id=wamid,
        status=msg_status,
        error_detail=error_detail,
        created_at=now
    )
    db.add(msg)

    conv.updated_at = now
    if conv.status == "new":
        conv.status = "open"

    db.commit()
    db.refresh(msg)

    # 3. Difundir por WebSocket para actualización en tiempo real en todos los agentes
    try:
        await ws_manager.broadcast_to_branch(conv.branch_id, {
            "type": "new_outgoing_message",
            "conversation_id": conv.id,
            "branch_id": conv.branch_id,
            "message": {
                "id": msg.id,
                "direction": msg.direction,
                "sender_type": msg.sender_type,
                "content": msg.content,
                "is_internal": msg.is_internal,
                "status": msg.status,
                "error_detail": msg.error_detail,
                "created_at": msg.created_at.isoformat()
            }
        })
    except Exception as ws_err:
        logger.error(f"Error difundiendo mensaje saliente por WebSocket: {ws_err}")

    return msg

@router.post("/{message_id}/retry", response_model=MessageResponse)
async def retry_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Reintenta el envío de un mensaje fallido a WhatsApp sin crear duplicados (Punto 10).
    """
    msg = db.query(Message).filter(Message.id == message_id, Message.deleted_at.is_(None)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")

    conv = check_conversation_access(db, msg.conversation_id, current_user, action="retry_message")

    if msg.status != "failed" or msg.direction != "outgoing":
        raise HTTPException(status_code=400, detail="Solo se pueden reintentar mensajes salientes con estado 'failed'.")

    contact = conv.contact
    if not contact or not contact.phone:
        raise HTTPException(status_code=400, detail="El contacto no tiene un teléfono válido.")

    wa_service = get_whatsapp_service()
    try:
        send_res = await wa_service.send_text_message(contact.phone, msg.content)
        wamid = None
        if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
            wamid = send_res["messages"][0].get("id")
        msg.whatsapp_message_id = wamid
        msg.status = "sent"
        msg.error_detail = None
        db.commit()
        db.refresh(msg)
        logger.info(f"Reintento exitoso de mensaje ID {msg.id}. WAMID: {wamid}")

        # Notificar actualización de estado
        await ws_manager.broadcast_to_branch(conv.branch_id, {
            "type": "message_status_updated",
            "message_id": msg.id,
            "conversation_id": conv.id,
            "status": "sent",
            "error_detail": None
        })
        return msg
    except Exception as e:
        msg.status = "failed"
        msg.error_detail = str(e)
        db.commit()
        db.refresh(msg)
        raise HTTPException(status_code=502, detail=f"Fallo al reintentar el envío a WhatsApp: {e}")

@router.delete("/{message_id}")
async def delete_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Borrado lógico de mensajes por administradores y supervisores (Punto 21).
    """
    if current_user.role not in ["admin", "supervisor"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores y supervisores tienen permiso para eliminar mensajes."
        )

    msg = db.query(Message).filter(Message.id == message_id, Message.deleted_at.is_(None)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")

    conv = check_conversation_access(db, msg.conversation_id, current_user, action="delete_message")

    now = datetime.now(timezone.utc)
    msg.deleted_at = now
    msg.deleted_by = current_user.id
    db.commit()

    conv_id = msg.conversation_id
    branch_id = conv.branch_id if conv else None

    logger.info(f"Mensaje ID {message_id} borrado lógicamente por {current_user.role.upper()} '{current_user.name}' (@{current_user.username}).")

    try:
        await ws_manager.broadcast_to_branch(branch_id, {
            "type": "message_deleted",
            "message_id": message_id,
            "conversation_id": conv_id
        })
    except Exception as ws_err:
        logger.error(f"Error difundiendo eliminación de mensaje por WebSocket: {ws_err}")

    return {"status": "deleted", "message_id": message_id}
