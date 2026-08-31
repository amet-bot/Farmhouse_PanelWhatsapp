import asyncio
import hmac
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, Response, HTTPException, status, Query, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from config import settings, mask_phone
from database import SessionLocal, get_db
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.branch import Branch
from services.whatsapp_service import get_whatsapp_service
from services.websocket_manager import ws_manager
from services.auto_responses import (
    WELCOME_MESSAGES, BRANCH_SELECTION_BODY, BRANCH_SELECTION_BUTTON,
    ACH_PAYMENT_INSTRUCTIONS, CARD_PAYMENT_MESSAGE, YAPPY_PAYMENT_MESSAGE, CASH_PAYMENT_MESSAGE,
    get_branch_welcome_message, SIMULATED_MENU_TEXT
)
from services.media_storage import save_media_bytes
from services.branch_matcher import match_branch_by_text
from services.order_flow_matcher import match_delivery_type_text, match_payment_method_text, mentions_cash
from services.push_service import notify_branch_new_message

logger = logging.getLogger("farmhouse.webhooks")

router = APIRouter(prefix="/webhooks", tags=["Webhooks Meta WhatsApp"])

def verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Valida la firma HMAC-SHA256 del webhook de Meta contra META_APP_SECRET (Punto 2).
    - En modo 'meta': META_APP_SECRET y el encabezado X-Hub-Signature-256 son estrictamente obligatorios.
    - En modo 'mock': Permite omitir la firma solo si no se envía encabezado ni secreto.
    """
    if settings.WHATSAPP_MODE == "meta":
        if not settings.META_APP_SECRET or not settings.META_APP_SECRET.strip():
            if settings.ENVIRONMENT == "production":
                logger.error("[Webhook Security] META_APP_SECRET no está configurado en producción. Rechazando solicitud.")
                return False
            # En modo desarrollo, permitir para pruebas locales si aún no se ha configurado el App Secret
            return True

        if not signature_header or not signature_header.strip():
            logger.warning("[Webhook Security] Encabezado X-Hub-Signature-256 ausente en solicitud de Meta.")
            return False

        parts = signature_header.split("=")
        if len(parts) != 2 or parts[0] != "sha256":
            logger.warning(f"[Webhook Security] Formato de firma inválido: '{signature_header}'")
            return False

        expected_sig = hmac.new(
            settings.META_APP_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(parts[1], expected_sig)
    else:
        # Modo mock
        if signature_header and settings.META_APP_SECRET:
            parts = signature_header.split("=")
            if len(parts) == 2 and parts[0] == "sha256":
                expected_sig = hmac.new(
                    settings.META_APP_SECRET.encode("utf-8"),
                    raw_body,
                    hashlib.sha256
                ).hexdigest()
                return hmac.compare_digest(parts[1], expected_sig)
        return True

async def _assign_conversation_branch(db: Session, conv: Conversation, branch: Branch, motivo: str) -> None:
    """Asigna una sucursal a una conversación, deja un mensaje de auditoría y difunde por WebSocket."""
    old_branch_id = conv.branch_id
    old_branch_name = conv.branch.name if conv.branch else "Sin asignar"
    conv.branch_id = branch.id
    conv.assigned_user_id = None
    conv.status = "unassigned"
    conv.updated_at = datetime.now(timezone.utc)
    audit_msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        sender_type="system",
        content=f"🔄 Sucursal asignada: {branch.name} (antes: {old_branch_name}). Motivo: {motivo}.",
        is_internal=True,
        status="sent"
    )
    db.add(audit_msg)
    db.commit()
    db.refresh(conv)
    logger.info(f"[BranchAssign] Conv ID {conv.id} asignada a sucursal {branch.id} ({branch.name}). Motivo: {motivo}.")
    for room_branch_id in {old_branch_id, branch.id}:
        if room_branch_id:
            await ws_manager.broadcast_to_branch(room_branch_id, {
                "type": "conversation_transferred",
                "conversation_id": conv.id,
                "branch_id": conv.branch_id
            })

async def _send_branch_selection_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, intro_text: Optional[str] = None) -> None:
    """Envía (opcionalmente) un mensaje introductorio y luego la lista interactiva de sucursales activas."""
    if intro_text:
        await asyncio.sleep(0.5)
        send_res = await wa_service.send_text_message(phone, intro_text)
        intro_wamid = None
        if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
            intro_wamid = send_res["messages"][0].get("id")
        intro_msg = Message(
            conversation_id=conv.id, direction="outgoing", sender_type="system",
            content=intro_text, whatsapp_message_id=intro_wamid, is_internal=False, status="sent"
        )
        db.add(intro_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(intro_msg)
        await ws_manager.broadcast_to_branch(conv.branch_id, {
            "type": "new_incoming_message",
            "conversation_id": conv.id,
            "branch_id": conv.branch_id,
            "contact_name": contact.name,
            "contact_phone": contact.phone,
            "message": {
                "id": intro_msg.id, "direction": intro_msg.direction, "sender_type": intro_msg.sender_type,
                "content": intro_msg.content, "status": intro_msg.status, "created_at": intro_msg.created_at.isoformat()
            },
            "is_new_conversation": False
        })

    await asyncio.sleep(0.5)
    active_branches = db.query(Branch).filter(Branch.active == True).order_by(Branch.name).all()
    if not active_branches:
        return
    rows = [{"id": f"branch_{b.id}", "title": b.name[:24]} for b in active_branches[:10]]
    menu_res = await wa_service.send_interactive_list(phone, BRANCH_SELECTION_BODY, BRANCH_SELECTION_BUTTON, rows)
    menu_wamid = None
    if isinstance(menu_res, dict) and "messages" in menu_res and menu_res["messages"]:
        menu_wamid = menu_res["messages"][0].get("id")
    menu_msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=f"📋 {BRANCH_SELECTION_BODY}", whatsapp_message_id=menu_wamid, is_internal=False, status="sent"
    )
    db.add(menu_msg)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(menu_msg)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": menu_msg.id, "direction": menu_msg.direction, "sender_type": menu_msg.sender_type,
            "content": menu_msg.content, "status": menu_msg.status, "created_at": menu_msg.created_at.isoformat()
        },
        "is_new_conversation": False
    })
    logger.info(f"[BranchSelection] Menú de {len(rows)} sucursales enviado a {mask_phone(phone)} para Conv ID {conv.id}.")

async def _send_interactive_buttons_message(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, body_text: str, buttons: list) -> None:
    """Envía un mensaje con botones de respuesta rápida (máx. 3) y lo registra/difunde."""
    send_res = await wa_service.send_interactive_buttons(phone, body_text, buttons)
    wamid = None
    if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
        wamid = send_res["messages"][0].get("id")
    msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=body_text, whatsapp_message_id=wamid, is_internal=False, status="sent"
    )
    db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": msg.id, "direction": msg.direction, "sender_type": msg.sender_type,
            "content": msg.content, "status": msg.status, "created_at": msg.created_at.isoformat()
        },
        "is_new_conversation": False
    })

async def _send_branch_welcome_and_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía el saludo específico de la sucursal y el menú simulado de opciones."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    welcome_text = get_branch_welcome_message(branch_name)

    # 1. Saludo de bienvenida a la sucursal seleccionada
    await asyncio.sleep(0.5)
    send_res = await wa_service.send_text_message(phone, welcome_text)
    wamid1 = None
    if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
        wamid1 = send_res["messages"][0].get("id")
    msg1 = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=welcome_text, whatsapp_message_id=wamid1, is_internal=False, status="sent"
    )
    db.add(msg1)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg1)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": msg1.id, "direction": msg1.direction, "sender_type": msg1.sender_type,
            "content": msg1.content, "status": msg1.status, "created_at": msg1.created_at.isoformat()
        },
        "is_new_conversation": False
    })

    # 2. Menú de opciones simulado
    await asyncio.sleep(0.5)
    send_res_menu = await wa_service.send_text_message(phone, SIMULATED_MENU_TEXT)
    wamid_menu = None
    if isinstance(send_res_menu, dict) and "messages" in send_res_menu and send_res_menu["messages"]:
        wamid_menu = send_res_menu["messages"][0].get("id")
    msg_menu = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=SIMULATED_MENU_TEXT, whatsapp_message_id=wamid_menu, is_internal=False, status="sent"
    )
    db.add(msg_menu)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg_menu)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": msg_menu.id, "direction": msg_menu.direction, "sender_type": msg_menu.sender_type,
            "content": msg_menu.content, "status": msg_menu.status, "created_at": msg_menu.created_at.isoformat()
        },
        "is_new_conversation": False
    })

    # 3. Pregunta interactiva de Delivery o Retiro
    await asyncio.sleep(0.5)
    delivery_buttons = [
        {"id": "delivery_delivery", "title": "🛵 Delivery"},
        {"id": "delivery_pickup", "title": "🏠 Retiro local"}
    ]
    await _send_interactive_buttons_message(
        db=db, wa_service=wa_service, conv=conv, contact=contact, phone=phone,
        body_text="¿Cómo te gustaría recibir tu pedido?", buttons=delivery_buttons
    )

async def _process_auto_flow_background(conv_id: int, contact_id: int, phone: str, msg_data: Dict[str, Any], msg_id: int):
    """
    Procesador en segundo plano para descargas de medios, lógica de estados y respuestas automáticas (Puntos 5 y 17).
    Desacoplado del ciclo HTTP para respuesta ultrarrápida a Meta.
    """
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conv_id, Conversation.deleted_at.is_(None)).first()
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        incoming_msg = db.query(Message).filter(Message.id == msg_id).first()
        if not conv or not contact:
            return

        wa_service = get_whatsapp_service()
        message_type = msg_data.get("message_type", "text")
        text = msg_data.get("text", "")

        # 1. Descargar archivo multimedia si existe
        if message_type != "text" and msg_data.get("media_id"):
            media_result = await wa_service.download_media(msg_data["media_id"])
            if media_result and incoming_msg:
                saved_url = save_media_bytes(media_result["bytes"], media_result["mime_type"])
                incoming_msg.media_url = saved_url
                incoming_msg.media_mime_type = media_result["mime_type"]
                db.commit()
                db.refresh(incoming_msg)
                logger.info(f"[Media Background] Archivo guardado para conv {conv.id}: {saved_url}")

                # Emitir actualización en tiempo real por WebSocket
                await ws_manager.broadcast_to_branch(conv.branch_id, {
                    "type": "message_media_updated",
                    "conversation_id": conv.id,
                    "branch_id": conv.branch_id,
                    "message_id": incoming_msg.id,
                    "media_url": saved_url,
                    "media_type": incoming_msg.media_type,
                    "media_mime_type": incoming_msg.media_mime_type
                })

        # 2. Si la conversación tiene automatización pausada por un agente, no responder
        if conv.automation_paused:
            logger.info(f"[AutoResponse] Automatización pausada para conv {conv.id}. Omitiendo bot.")
            return

        # 3. Detección de sucursal si aún no tiene asignada
        branch_matched_now = False
        matched_via = None
        if conv.branch_id is None:
            matched_branch = None
            interactive_id = msg_data.get("interactive_id", "")
            if message_type == "interactive" and interactive_id.startswith("branch_"):
                try:
                    selected_branch_id = int(interactive_id.replace("branch_", ""))
                    matched_branch = db.query(Branch).filter(Branch.id == selected_branch_id, Branch.active == True).first()
                except Exception:
                    pass
                matched_via = "interactive"
            elif message_type == "text":
                active_branches = db.query(Branch).filter(Branch.active == True).all()
                matched_branch = match_branch_by_text(text, active_branches)
                matched_via = "text"

            if matched_branch:
                motivo = "el cliente tocó el menú" if matched_via == "interactive" else "el cliente escribió el nombre en texto"
                await _assign_conversation_branch(db, conv, matched_branch, motivo)
                branch_matched_now = True

        # 4. Flujo conversacional estructurado
        resolved_this_turn = branch_matched_now
        if conv.branch_id is not None and conv.delivery_type is None and not branch_matched_now:
            matched_delivery = None
            interactive_id = msg_data.get("interactive_id", "") if message_type == "interactive" else ""
            if interactive_id == "delivery_delivery":
                matched_delivery = "delivery"
            elif interactive_id == "delivery_pickup":
                matched_delivery = "pickup"
            elif message_type == "text":
                matched_delivery = match_delivery_type_text(text)

            if matched_delivery:
                conv.delivery_type = matched_delivery
                conv.updated_at = datetime.now(timezone.utc)
                db.commit()
                resolved_this_turn = True
                logger.info(f"[DeliverySelection] Conv ID {conv.id}: Tipo de entrega '{matched_delivery}' detectado.")

        elif conv.branch_id is not None and conv.delivery_type is not None and conv.payment_method is None:
            matched_payment = None
            interactive_id = msg_data.get("interactive_id", "") if message_type == "interactive" else ""
            if interactive_id.startswith("pay_"):
                matched_payment = interactive_id.replace("pay_", "")
            elif message_type == "text":
                matched_payment = match_payment_method_text(text)

            if matched_payment:
                conv.payment_method = matched_payment
                conv.updated_at = datetime.now(timezone.utc)
                db.commit()
                resolved_this_turn = True
                logger.info(f"[PaymentSelection] Conv ID {conv.id}: Método de pago '{matched_payment}' detectado.")

        # 5. Respuestas automáticas correspondientes
        if conv.branch_id is None:
            now = datetime.now(timezone.utc)
            should_prompt = True
            if conv.last_branch_prompt_at:
                elapsed = (now - conv.last_branch_prompt_at.replace(tzinfo=timezone.utc)).total_seconds() if conv.last_branch_prompt_at.tzinfo else (datetime.utcnow() - conv.last_branch_prompt_at).total_seconds()
                if elapsed < 180 and not (message_type == "text" and any(k in text.lower() for k in ["hola", "menu", "sucursal", "buenas"])):
                    should_prompt = False

            if should_prompt:
                intro = WELCOME_MESSAGES[0]
                await _send_branch_selection_menu(db, wa_service, conv, contact, phone, intro_text=intro)
                conv.last_branch_prompt_at = now
                db.commit()
            return

        if branch_matched_now:
            await _send_branch_welcome_and_menu(db, wa_service, conv, contact, phone)
            return

        if conv.delivery_type is not None and conv.payment_method is None and resolved_this_turn:
            payment_rows = [
                {"id": "pay_yappy", "title": "📱 Yappy"},
                {"id": "pay_card", "title": "💳 Tarjeta (Link)"},
                {"id": "pay_ach", "title": "🏦 Transferencia ACH"},
                {"id": "pay_cash", "title": "💵 Efectivo"},
            ]
            await asyncio.sleep(0.5)
            await wa_service.send_interactive_list(
                phone,
                "¿Cómo te gustaría pagar tu pedido?",
                "Ver métodos de pago",
                payment_rows
            )
            return

        if resolved_this_turn and conv.payment_method is not None:
            payment_closing_messages = {
                "ach": ACH_PAYMENT_INSTRUCTIONS,
                "card": CARD_PAYMENT_MESSAGE,
                "yappy": YAPPY_PAYMENT_MESSAGE,
                "cash": CASH_PAYMENT_MESSAGE,
            }
            closing_text = payment_closing_messages.get(
                conv.payment_method,
                "¡Genial! Ya tenemos todo listo para arrancar 😊 En un momento alguien de nuestro equipo te atiende para tomar los detalles de tu pedido. ¡Gracias por tu paciencia!"
            )
            send_res = await wa_service.send_text_message(phone, closing_text)
            wamid = None
            if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
                wamid = send_res["messages"][0].get("id")
            msg = Message(
                conversation_id=conv.id, direction="outgoing", sender_type="system",
                content=closing_text, whatsapp_message_id=wamid, is_internal=False, status="sent"
            )
            db.add(msg)
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(msg)
            await ws_manager.broadcast_to_branch(conv.branch_id, {
                "type": "new_incoming_message",
                "conversation_id": conv.id,
                "branch_id": conv.branch_id,
                "contact_name": contact.name,
                "contact_phone": contact.phone,
                "message": {
                    "id": msg.id, "direction": msg.direction, "sender_type": msg.sender_type,
                    "content": msg.content, "status": msg.status, "created_at": msg.created_at.isoformat()
                },
                "is_new_conversation": False
            })
    except Exception as e:
        logger.error(f"[AutoResponse Background Error] Conv ID {conv_id}: {e}", exc_info=True)
    finally:
        db.close()

def _send_push_notification_background(branch_id: int, conversation_id: int, contact_name: str, message_preview: str):
    """
    Envía notificaciones push del navegador a los encargados de la sucursal (y supervisores/admins)
    cuando llega un mensaje nuevo. Se ejecuta desacoplado del ciclo de respuesta HTTP a Meta.
    """
    db = SessionLocal()
    try:
        notify_branch_new_message(
            db=db,
            branch_id=branch_id,
            title=f"💬 {contact_name}",
            body=message_preview,
            conversation_id=conversation_id
        )
    except Exception as e:
        logger.error(f"[Push Background Error] Conv ID {conversation_id}: {e}", exc_info=True)
    finally:
        db.close()

@router.get("/whatsapp")
def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    challenge: str = Query(None, alias="hub.challenge"),
    verify_token: str = Query(None, alias="hub.verify_token")
):
    """
    Endpoint de handshake requerido por Meta WhatsApp Cloud API para verificar el webhook.
    """
    if mode == "subscribe" and verify_token == settings.META_WA_VERIFY_TOKEN:
        logger.info("Webhook de Meta verificado exitosamente.")
        return Response(content=challenge, media_type="text/plain")
    logger.warning("Intento de verificación de webhook fallido.")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verificación de token inválida.")

@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Recepción oficial de eventos, estados y mensajes entrantes desde Meta WhatsApp Cloud API (Puntos 2, 4, 5, 10).
    - Valida firma HMAC-SHA256 de forma obligatoria en modo Meta.
    - Idempotencia estricta por whatsapp_message_id.
    - Desacoplamiento asíncrono con BackgroundTasks para respuesta < 100ms a Meta.
    - Procesa eventos de estado (sent, delivered, read, failed).
    """
    raw_body = await request.body()

    # 1. Validación de firma estricta (Punto 2)
    sig_header = request.headers.get("X-Hub-Signature-256")
    if not verify_meta_signature(raw_body, sig_header):
        logger.warning("Firma de webhook de Meta inválida o ausente.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma de webhook de Meta inválida."
        )

    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="JSON payload inválido.")

    wa_service = get_whatsapp_service()

    # 2. Verificar si es un evento de actualización de estado (delivered, read, failed)
    status_data = wa_service.parse_incoming_status(payload)
    if status_data:
        wamid = status_data["wamid"]
        new_status = status_data["status"]
        err = status_data.get("error")

        msg = db.query(Message).filter(Message.whatsapp_message_id == wamid).first()
        if msg:
            msg.status = new_status
            if err:
                msg.error_detail = err
            db.commit()
            conv_id = msg.conversation_id
            branch_id = msg.conversation.branch_id if msg.conversation else None

            # Notificar a través de WebSocket a los operadores
            await ws_manager.broadcast_to_branch(branch_id, {
                "type": "message_status_updated",
                "message_id": msg.id,
                "conversation_id": conv_id,
                "status": new_status,
                "error_detail": err
            })
            logger.info(f"[Meta Status Update] Mensaje WAMID {wamid} actualizado a '{new_status}'")
            return {"status": "status_updated", "wamid": wamid, "message_status": new_status}
        return {"status": "status_ignored"}

    # 3. Procesar mensaje entrante
    msg_data = wa_service.parse_incoming_message(payload)
    if not msg_data:
        return {"status": "ignored"}

    phone = msg_data["from_phone"]
    if not phone.startswith("+"):
        phone = f"+{phone}"
    contact_name = msg_data["contact_name"]
    wamid = msg_data["wamid"]
    message_type = msg_data.get("message_type", "text")

    type_labels = {
        "image": "📷 Imagen",
        "video": "🎥 Video",
        "audio": "🎤 Audio",
        "document": "📄 Documento",
        "sticker": "🩹 Sticker",
    }
    if message_type in ("text", "interactive"):
        text = msg_data["text"]
    else:
        caption = msg_data.get("caption")
        text = caption if caption else type_labels.get(message_type, f"[{message_type}]")

    try:
        # 4. Comprobación de IDEMPOTENCIA previa (Punto 4)
        if wamid:
            existing_message = db.query(Message).filter(Message.whatsapp_message_id == wamid).first()
            if existing_message:
                logger.info(f"[Idempotency] Mensaje WAMID {wamid} ya fue procesado anteriormente. Omitiendo duplicado.")
                return {"status": "duplicate", "detail": "Message already processed"}


        # 5. Contacto
        contact = db.query(Contact).filter(Contact.phone == phone).first()
        now = datetime.now(timezone.utc)
        if not contact:
            contact = Contact(name=contact_name, phone=phone, created_at=now, last_interaction=now)
            db.add(contact)
            db.flush()
        else:
            contact.last_interaction = now
            if contact.deleted_at:
                contact.deleted_at = None

        # 6. Conversación activa
        conv = db.query(Conversation).filter(
            Conversation.customer_id == contact.id,
            Conversation.status.in_(["new", "unassigned", "open", "pending"]),
            Conversation.deleted_at.is_(None)
        ).order_by(Conversation.updated_at.desc()).first()

        is_new_conv = False
        if not conv:
            conv = Conversation(
                customer_id=contact.id,
                branch_id=None,
                status="unassigned",
                created_at=now,
                updated_at=now
            )
            db.add(conv)
            db.flush()
            is_new_conv = True

        # 7. Insertar mensaje entrante de forma atómica
        message = Message(
            conversation_id=conv.id,
            direction="incoming",
            sender_type="customer",
            content=text,
            whatsapp_message_id=wamid,
            is_internal=False,
            status="delivered",
            media_type=message_type if message_type != "text" else None,
            created_at=now
        )
        db.add(message)
        conv.updated_at = now
        db.commit()
        db.refresh(message)
        db.refresh(conv)

        # 8. Difundir evento de nuevo mensaje inmediatamente a agentes por WebSocket
        await ws_manager.broadcast_to_branch(conv.branch_id, {
            "type": "new_incoming_message",
            "conversation_id": conv.id,
            "branch_id": conv.branch_id,
            "contact_name": contact.name,
            "contact_phone": contact.phone,
            "message": {
                "id": message.id,
                "direction": message.direction,
                "sender_type": message.sender_type,
                "content": message.content,
                "status": message.status,
                "created_at": message.created_at.isoformat()
            },
            "is_new_conversation": is_new_conv
        })

        # 9. Encolar tareas en segundo plano (Descarga de medios + Secuencia del Bot) (Punto 5)
        background_tasks.add_task(
            _process_auto_flow_background,
            conv_id=conv.id,
            contact_id=contact.id,
            phone=phone,
            msg_data=msg_data,
            msg_id=message.id
        )

        # 10. Notificación push a los encargados de la sucursal (si ya tiene una asignada)
        if conv.branch_id:
            background_tasks.add_task(
                _send_push_notification_background,
                branch_id=conv.branch_id,
                conversation_id=conv.id,
                contact_name=contact.name,
                message_preview=text
            )

        return {
            "status": "received",
            "conversation_id": conv.id,
            "message_id": message.id
        }

    except IntegrityError as ie:
        db.rollback()
        logger.warning(f"[Idempotency Race Condition] Violación de unicidad para WAMID {wamid}: {ie}")
        return {"status": "duplicate", "detail": "Message already processed concurrently"}
    except Exception as e:
        db.rollback()
        logger.error(f"[Webhook Error] Error procesando webhook entrante: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno al procesar webhook.")
