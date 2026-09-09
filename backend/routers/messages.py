import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from sqlalchemy.orm import Session

from config import BASE_DIR
from database import get_db
from models.message import Message
from models.conversation import Conversation
from models.user import User
from schemas.message import MessageResponse, MessageCreate
from security.auth import get_current_authorized_user
from security.access_control import check_conversation_access
from services.whatsapp_service import get_whatsapp_service
from services.websocket_manager import ws_manager
from services.media_storage import save_media_bytes, MEDIA_DOWNLOAD_FAILED_MARKER

logger = logging.getLogger("farmhouse.messages")

router = APIRouter(prefix="/messages", tags=["Mensajes"])

OUTGOING_FILE_TYPES = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".pdf": ("document", "application/pdf"),
    ".doc": ("document", "application/msword"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xls": ("document", "application/vnd.ms-excel"),
    ".xlsx": ("document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".ppt": ("document", "application/vnd.ms-powerpoint"),
    ".pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".txt": ("document", "text/plain"),
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


def _extract_wamid(send_res) -> str | None:
    if isinstance(send_res, dict) and send_res.get("messages"):
        return send_res["messages"][0].get("id")
    return None


def _stored_media_path(media_url: str) -> Path | None:
    """Resuelve una URL /media/... sin permitir salir del directorio multimedia."""
    relative = str(media_url or "").split("?", 1)[0].lstrip("/")
    if relative.startswith("media/"):
        relative = relative[len("media/"):]
    media_root = (BASE_DIR / "media").resolve()
    candidate = (media_root / relative).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None

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


@router.post("/media", response_model=MessageResponse)
async def send_media_message(
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
    caption: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Envía imágenes y documentos permitidos desde el panel mediante WhatsApp Cloud API."""
    conv = check_conversation_access(db, conversation_id, current_user, action="send_message")
    contact = conv.contact
    if not contact or not contact.phone:
        raise HTTPException(status_code=400, detail="El contacto no tiene un número de teléfono registrado.")

    original_name = str(file.filename or "archivo").replace("\\", "/").split("/")[-1]
    original_name = "".join(ch for ch in original_name if ch >= " " and ch not in {'"'}).strip()[:240]
    suffix = Path(original_name).suffix.lower()
    type_info = OUTGOING_FILE_TYPES.get(suffix)
    if not original_name or not type_info:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato no permitido. Usa JPG, PNG, PDF, Word, Excel, PowerPoint o TXT.",
        )

    media_type, mime_type = type_info
    max_bytes = MAX_IMAGE_BYTES if media_type == "image" else MAX_DOCUMENT_BYTES
    media_bytes = await file.read(max_bytes + 1)
    await file.close()
    if not media_bytes:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(media_bytes) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"El archivo supera el límite de {max_mb} MB.")

    clean_caption = str(caption or "").strip()
    if len(clean_caption) > 1024:
        raise HTTPException(status_code=422, detail="El texto del archivo no puede superar 1024 caracteres.")

    # Guardar una copia para que todos los agentes puedan verla en el historial del panel.
    local_url = save_media_bytes(media_bytes, mime_type)
    wamid = None
    uploaded_media_id = None
    msg_status = "sent"
    error_detail = None
    try:
        send_res = await get_whatsapp_service().send_media_message(
            contact.phone,
            media_bytes,
            mime_type,
            media_type,
            filename=original_name,
            caption=clean_caption or None,
        )
        wamid = _extract_wamid(send_res)
        uploaded_media_id = send_res.get("uploaded_media_id") if isinstance(send_res, dict) else None
    except Exception as e:
        msg_status = "failed"
        error_detail = str(e)
        logger.error(f"Fallo enviando archivo a WhatsApp para conversación {conv.id}: {e}")

    now = datetime.now(timezone.utc)
    content = original_name if media_type == "document" else (clean_caption or "📷 Imagen")
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        sender_type="agent",
        sender_id=current_user.id,
        content=content,
        is_internal=False,
        whatsapp_message_id=wamid,
        status=msg_status,
        error_detail=error_detail,
        media_url=local_url,
        media_type=media_type,
        media_mime_type=mime_type,
        media_id=uploaded_media_id,
        created_at=now,
    )
    db.add(msg)
    conv.updated_at = now
    if conv.status == "new":
        conv.status = "open"
    db.commit()
    db.refresh(msg)

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
                "media_url": msg.media_url,
                "media_type": msg.media_type,
                "media_mime_type": msg.media_mime_type,
                "created_at": msg.created_at.isoformat(),
            },
        })
    except Exception as ws_err:
        logger.error(f"Error difundiendo archivo saliente por WebSocket: {ws_err}")

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
        if msg.media_type in ("image", "document") and msg.media_url:
            stored_path = _stored_media_path(msg.media_url)
            if not stored_path:
                raise HTTPException(status_code=410, detail="La copia local del archivo ya no está disponible.")
            media_bytes = stored_path.read_bytes()
            retry_caption = msg.content if msg.media_type == "image" and msg.content != "📷 Imagen" else None
            send_res = await wa_service.send_media_message(
                contact.phone,
                media_bytes,
                msg.media_mime_type or "application/octet-stream",
                msg.media_type,
                filename=msg.content if msg.media_type == "document" else stored_path.name,
                caption=retry_caption,
            )
            if isinstance(send_res, dict):
                msg.media_id = send_res.get("uploaded_media_id") or msg.media_id
        else:
            send_res = await wa_service.send_text_message(contact.phone, msg.content)
        wamid = _extract_wamid(send_res)
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

@router.post("/{message_id}/retry-media", response_model=MessageResponse)
async def retry_message_media(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Reintenta la descarga de un adjunto multimedia entrante que falló (imagen, video, audio,
    documento). Reutiliza el media_id guardado del webhook original en vez de volver a parsear
    el payload de Meta. Idempotente: si el archivo ya está disponible, no vuelve a descargarlo.
    """
    msg = db.query(Message).filter(Message.id == message_id, Message.deleted_at.is_(None)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")

    conv = check_conversation_access(db, msg.conversation_id, current_user, action="retry_media")

    if msg.direction != "incoming" or not msg.media_type:
        raise HTTPException(status_code=400, detail="Este mensaje no tiene un adjunto multimedia entrante para reintentar.")

    if msg.media_url:
        # Ya disponible (por ejemplo otro agente ya lo reintentó exitosamente entre tanto):
        # no volver a descargar de Meta innecesariamente.
        return msg

    if not msg.media_id:
        raise HTTPException(status_code=400, detail="No se guardó un identificador de archivo para este mensaje; no se puede reintentar.")

    wa_service = get_whatsapp_service()
    try:
        media_result = await wa_service.download_media(msg.media_id)
    except Exception as e:
        logger.error(f"[RetryMedia] Error descargando media_id={msg.media_id} (mensaje {msg.id}): {e}")
        media_result = None

    if not media_result:
        msg.error_detail = MEDIA_DOWNLOAD_FAILED_MARKER
        db.commit()
        db.refresh(msg)
        raise HTTPException(status_code=502, detail="No se pudo descargar el archivo desde WhatsApp. Intenta de nuevo en unos minutos.")

    saved_url = save_media_bytes(media_result["bytes"], media_result["mime_type"])
    msg.media_url = saved_url
    msg.media_mime_type = media_result["mime_type"]
    msg.error_detail = None
    db.commit()
    db.refresh(msg)
    logger.info(f"[RetryMedia] Reintento exitoso para mensaje {msg.id}: {saved_url}")

    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "message_media_updated",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "message_id": msg.id,
        "media_url": msg.media_url,
        "media_type": msg.media_type,
        "media_mime_type": msg.media_mime_type,
        "media_failed": False
    })

    return msg

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
        payload = {
            "type": "message_deleted",
            "message_id": message_id,
            "conversation_id": conv_id
        }
        if branch_id:
            await ws_manager.broadcast_to_branch(branch_id, payload)
        else:
            await ws_manager.broadcast_all(payload)
    except Exception as ws_err:
        logger.error(f"Error difundiendo eliminación de mensaje por WebSocket: {ws_err}")

    return {"status": "deleted", "message_id": message_id}
