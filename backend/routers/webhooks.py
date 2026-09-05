import asyncio
import hmac
import hashlib
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, Response, HTTPException, status, Query, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from config import settings, mask_phone, get_whatsapp_number_for_branch
from database import SessionLocal, get_db
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.branch import Branch
from services.whatsapp_service import get_whatsapp_service
from services.websocket_manager import ws_manager
from services.auto_responses import (
    MAIN_WELCOME_BODY, MAIN_MENU_BUTTON, MAIN_MENU_OPTIONS, MAIN_MENU_TEXT_FALLBACK,
    BRANCH_SELECTION_BODY, BRANCH_SELECTION_VISIT_BODY, BRANCH_SELECTION_DELIVERY_BODY,
    BRANCH_SELECTION_PICKUP_BODY, BRANCH_SELECTION_BUTTON, CORPORATE_WELCOME_MESSAGE,
    get_branch_visit_message, get_branch_welcome_message,
    ACH_PAYMENT_INSTRUCTIONS, CARD_PAYMENT_MESSAGE, YAPPY_PAYMENT_MESSAGE, CASH_PAYMENT_MESSAGE
)
from services.media_storage import save_media_bytes, MEDIA_DOWNLOAD_FAILED_MARKER
from services.branch_matcher import match_branch_by_text
from services.order_flow_matcher import (
    match_main_option, match_delivery_type_text, match_payment_method_text, mentions_cash
)
from services.push_service import notify_branch_new_message
from security.auth import create_menu_session_token

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

async def _send_main_welcome_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía el menú principal de bienvenida de Farmhouse con las 4 opciones interactivas."""
    await asyncio.sleep(0.3)
    menu_res = await wa_service.send_interactive_list(
        phone,
        MAIN_WELCOME_BODY,
        MAIN_MENU_BUTTON,
        MAIN_MENU_OPTIONS
    )
    menu_wamid = None
    if isinstance(menu_res, dict) and "messages" in menu_res and menu_res["messages"]:
        menu_wamid = menu_res["messages"][0].get("id")

    msg_content = MAIN_WELCOME_BODY
    menu_msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=msg_content, whatsapp_message_id=menu_wamid, is_internal=False, status="sent"
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
    logger.info(f"[MainMenu] Menú principal de 4 opciones enviado a {mask_phone(phone)} para Conv ID {conv.id}.")

async def _send_branch_selection_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, intro_text: Optional[str] = None, prompt_body: Optional[str] = None) -> None:
    """Envía (opcionalmente) un mensaje introductorio y luego la lista interactiva de sucursales activas."""
    body_text = prompt_body or BRANCH_SELECTION_BODY
    if intro_text:
        await asyncio.sleep(0.3)
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

    await asyncio.sleep(0.3)
    active_branches = db.query(Branch).filter(Branch.active == True).order_by(Branch.name).all()
    if not active_branches:
        return
    rows = [{"id": f"branch_{b.id}", "title": b.name[:24], "description": f"Sucursal {b.name}"[:72]} for b in active_branches if b.code != "CAT"][:10]
    if not rows:
        rows = [{"id": f"branch_{b.id}", "title": b.name[:24]} for b in active_branches[:10]]

    menu_res = await wa_service.send_interactive_list(phone, body_text, BRANCH_SELECTION_BUTTON, rows)
    menu_wamid = None
    if isinstance(menu_res, dict) and "messages" in menu_res and menu_res["messages"]:
        menu_wamid = menu_res["messages"][0].get("id")
    menu_msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=f"📋 {body_text}", whatsapp_message_id=menu_wamid, is_internal=False, status="sent"
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
    """Envía la información de visita o el enlace del Menú Digital según el tipo de atención."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    branch_code = conv.branch.code if conv.branch else ""

    if conv.delivery_type == "visit":
        visit_text = get_branch_visit_message(branch_code, branch_name)
        send_res = await wa_service.send_text_message(phone, visit_text)
        wamid = None
        if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
            wamid = send_res["messages"][0].get("id")
        msg = Message(
            conversation_id=conv.id, direction="outgoing", sender_type="system",
            content=visit_text, whatsapp_message_id=wamid, is_internal=False, status="sent"
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
        return

    # Enlace personalizado al Menú Digital (/menu)
    await asyncio.sleep(0.3)
    client_name = urllib.parse.quote(contact.name or "")
    client_phone = urllib.parse.quote(phone.lstrip("+"))
    try:
        origin_wa = get_whatsapp_number_for_branch(branch_code)
    except RuntimeError:
        origin_wa = None
    wa_param = f"&wa={origin_wa}" if origin_wa else ""
    session_token = create_menu_session_token(conv.id, conv.branch_id)
    menu_url = f"{settings.PUBLIC_BASE_URL}/menu?branch={branch_code}&phone={client_phone}&name={client_name}&conv={conv.id}&session={session_token}{wa_param}"
    
    if conv.delivery_type == "delivery":
        menu_text = (
            f"¡Excelente! 🛵 Aquí tienes nuestro Menú Digital para pedir a domicilio desde Farmhouse *{branch_name}*:\n\n"
            f"👉 *Toca aquí para ver nuestro Menú y hacer tu pedido:* 👇\n"
            f"{menu_url}\n\n"
            f"_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos, ingresa tu dirección y envíanos tu orden en 1 clic._"
        )
    elif conv.delivery_type == "pickup":
        menu_text = (
            f"¡Perfecto! 🛍️ Aquí tienes nuestro Menú Digital para retirar en Farmhouse *{branch_name}*:\n\n"
            f"👉 *Toca aquí para ver nuestro Menú y hacer tu pedido:* 👇\n"
            f"{menu_url}\n\n"
            f"_Elige tus platillos favoritos y te lo tendremos fresco y listo cuando pases a retirarlo._"
        )
    else:
        menu_text = (
            f"¡Bienvenido a Farmhouse *{branch_name}*! 🌿🥗\n\n"
            f"👉 *Toca aquí para ver nuestro Menú Interactivo y hacer tu pedido:* 👇\n"
            f"{menu_url}\n\n"
            f"_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos y envíanos tu orden con Delivery o Retiro en 1 clic._"
        )
    
    send_res_menu = await wa_service.send_text_message(phone, menu_text)
    wamid_menu = None
    if isinstance(send_res_menu, dict) and "messages" in send_res_menu and send_res_menu["messages"]:
        wamid_menu = send_res_menu["messages"][0].get("id")
    msg_menu = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=menu_text, whatsapp_message_id=wamid_menu, is_internal=False, status="sent"
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

        # 0. Pedido estructurado enviado desde la Web App de Menú (/menu). Ya trae sucursal,
        #    entrega y pago resueltos (ver POST /api/orders/public), así que respondemos con un mensaje
        #    cálido y empático según el tipo de entrega (Delivery o Retiro) y pausamos el bot para que
        #    el agente de la sucursal tome el control personal de la conversación.
        if message_type == "text" and "MI PEDIDO FARMHOUSE" in text.upper():
            is_delivery = "DELIVERY" in text.upper() or (conv.delivery_type == "delivery")
            is_card = "TARJETA" in text.upper() or (conv.payment_method == "card")
            branch_name = conv.branch.name if conv.branch else "Farmhouse"

            if is_delivery:
                if is_card:
                    confirmation_text = (
                        f"¡Hola! Muchas gracias por tu pedido 🌿🥗 Ya lo tenemos registrado con éxito en nuestro sistema.\n\n"
                        f"🛵 Como seleccionaste entrega a *Delivery* y método de pago con *Tarjeta*, nuestro agente encargado en la sucursal de *{branch_name}* "
                        f"está revisando tu dirección en este momento para confirmarte el costo del envío y en seguida te compartirá por este medio el *enlace de pago seguro* para que puedas pagar cómodamente.\n\n"
                        f"Por favor regálanos unos breves minutos de paciencia mientras te preparamos el enlace y coordinamos tu entrega. ¡Un verdadero placer atenderte! 😊💳✨"
                    )
                else:
                    confirmation_text = (
                        f"¡Hola! Muchas gracias por tu pedido 🌿🥗 Ya lo tenemos registrado en nuestro sistema.\n\n"
                        f"🛵 Como seleccionaste entrega a *Delivery*, nuestro agente encargado en la sucursal de *{branch_name}* "
                        f"está revisando tu dirección en este momento para confirmarte el costo exacto del envío y el monto total final.\n\n"
                        f"Por favor regálanos unos breves minutos de paciencia, en seguida te estaremos atendiendo personalmente con todo el gusto del mundo para coordinar tu entrega y el pago. ¡Un placer atenderte! 😊✨"
                    )
            else:
                if is_card:
                    confirmation_text = (
                        f"¡Hola! Muchas gracias por tu pedido 🌿🥗 Ya lo tenemos registrado con éxito para *Retiro en sucursal ({branch_name})*.\n\n"
                        f"💳 Como seleccionaste pago con *Tarjeta*, en unos breves minutos nuestro agente encargado te enviará por aquí el *enlace de pago seguro* para que puedas abonarlo antes de pasar a retirarlo fresco y listo.\n\n"
                        f"¡Muchas gracias por tu paciencia y por elegir Farmhouse! 😊💳✨"
                    )
                else:
                    confirmation_text = (
                        f"¡Hola! Muchas gracias por tu pedido 🌿🥗 Ya lo tenemos registrado con éxito para *Retiro en sucursal ({branch_name})*.\n\n"
                        f"En breve nuestro equipo te confirmará el tiempo estimado de preparación para que puedas pasar a retirarlo fresco y recién preparado.\n\n"
                        f"Si seleccionaste pagar por Yappy o ACH, puedes compartirnos tu comprobante por este medio cuando gustes 📸. ¡Muchas gracias por tu paciencia y preferencia! 😊✨"
                    )

            send_res = await wa_service.send_text_message(phone, confirmation_text)
            wamid = None
            if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
                wamid = send_res["messages"][0].get("id")
            confirmation_msg = Message(
                conversation_id=conv.id, direction="outgoing", sender_type="system",
                content=confirmation_text, whatsapp_message_id=wamid, is_internal=False, status="sent"
            )
            db.add(confirmation_msg)
            conv.automation_paused = True
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(confirmation_msg)
            await ws_manager.broadcast_to_branch(conv.branch_id, {
                "type": "new_incoming_message",
                "conversation_id": conv.id,
                "branch_id": conv.branch_id,
                "contact_name": contact.name,
                "contact_phone": contact.phone,
                "message": {
                    "id": confirmation_msg.id, "direction": confirmation_msg.direction,
                    "sender_type": confirmation_msg.sender_type, "content": confirmation_msg.content,
                    "status": confirmation_msg.status, "created_at": confirmation_msg.created_at.isoformat()
                },
                "is_new_conversation": False
            })
            return

        # 0.1 Atención humana solicitada explícitamente por el cliente
        if message_type == "text":
            text_lower = text.lower().strip()
            human_keywords = [
                "hablar con alguien", "hablar con una persona", "agente", "asesor",
                "humano", "atencion humana", "persona real", "quiero que me atienda alguien",
                "quiero hablar con un humano"
            ]
            if any(kw in text_lower for kw in human_keywords):
                branch_name = conv.branch.name if conv.branch else "Farmhouse"
                human_response = (
                    f"¡Con mucho gusto! 🤝 Te comunicamos de inmediato con un agente de nuestro equipo en *{branch_name}*.\n\n"
                    f"Por favor regálanos unos minutos de paciencia mientras uno de nuestros compañeros revisa tu conversación "
                    f"para atenderte personalmente por aquí. ¡Muchas gracias por esperarnos! 😊"
                )
                send_res = await wa_service.send_text_message(phone, human_response)
                wamid = None
                if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
                    wamid = send_res["messages"][0].get("id")
                human_msg = Message(
                    conversation_id=conv.id, direction="outgoing", sender_type="system",
                    content=human_response, whatsapp_message_id=wamid, is_internal=False, status="sent"
                )
                db.add(human_msg)
                conv.automation_paused = True
                conv.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(human_msg)
                await ws_manager.broadcast_to_branch(conv.branch_id, {
                    "type": "new_incoming_message",
                    "conversation_id": conv.id,
                    "branch_id": conv.branch_id,
                    "contact_name": contact.name,
                    "contact_phone": contact.phone,
                    "message": {
                        "id": human_msg.id, "direction": human_msg.direction,
                        "sender_type": human_msg.sender_type, "content": human_msg.content,
                        "status": human_msg.status, "created_at": human_msg.created_at.isoformat()
                    },
                    "is_new_conversation": False
                })
                return

        # 1. Descargar archivo multimedia si existe y aún no fue descargado inline
        if message_type != "text" and msg_data.get("media_id"):
            if incoming_msg and not incoming_msg.media_url:
                media_result = await wa_service.download_media(msg_data["media_id"])
                if media_result:
                    saved_url = save_media_bytes(media_result["bytes"], media_result["mime_type"])
                    incoming_msg.media_url = saved_url
                    incoming_msg.media_mime_type = media_result["mime_type"]
                    incoming_msg.error_detail = None
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
                        "media_mime_type": incoming_msg.media_mime_type,
                        "media_failed": False
                    })
                else:
                    # La descarga inline (rápida) ya había fallado/expirado y este reintento en
                    # background también falló: en vez de dejar el mensaje eternamente en
                    # "Descargando..." (lo que reportó el usuario), se marca el fallo explícito
                    # para que el panel muestre un estado de error con botón "Reintentar" (que
                    # llama a POST /messages/{id}/retry-media reutilizando media_id).
                    incoming_msg.error_detail = MEDIA_DOWNLOAD_FAILED_MARKER
                    db.commit()
                    logger.warning(f"[Media Background] No se pudo descargar media_id={msg_data['media_id']} para conv {conv.id} (mensaje {incoming_msg.id}).")

                    await ws_manager.broadcast_to_branch(conv.branch_id, {
                        "type": "message_media_updated",
                        "conversation_id": conv.id,
                        "branch_id": conv.branch_id,
                        "message_id": incoming_msg.id,
                        "media_url": None,
                        "media_type": incoming_msg.media_type,
                        "media_mime_type": incoming_msg.media_mime_type,
                        "media_failed": True
                    })

        # 2. Si la conversación tiene automatización pausada por un agente, no responder
        if conv.automation_paused:
            logger.info(f"[AutoResponse] Automatización pausada para conv {conv.id}. Omitiendo bot.")
            return

        # 3. Detección de opción del Menú Principal (1. Visitar, 2. Delivery, 3. Retiro, 4. Corporativo)
        interactive_id = str(msg_data.get("interactive_id") or "")
        main_option_matched = None
        if interactive_id == "opt_visit":
            main_option_matched = "visit"
        elif interactive_id == "opt_delivery":
            main_option_matched = "delivery"
        elif interactive_id == "opt_pickup":
            main_option_matched = "pickup"
        elif interactive_id == "opt_corporate":
            main_option_matched = "corporate"
        elif message_type == "text":
            main_option_matched = match_main_option(text)

        # 3.1 Opción 4: Pedido Corporativo / Evento
        if main_option_matched == "corporate":
            cat_branch = db.query(Branch).filter((Branch.code == "CAT") | (Branch.name.ilike("%catering%"))).first()
            if cat_branch:
                await _assign_conversation_branch(db, conv, cat_branch, "cliente seleccionó Pedido Corporativo / Evento")

            send_res = await wa_service.send_text_message(phone, CORPORATE_WELCOME_MESSAGE)
            wamid = None
            if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
                wamid = send_res["messages"][0].get("id")
            corp_msg = Message(
                conversation_id=conv.id, direction="outgoing", sender_type="system",
                content=CORPORATE_WELCOME_MESSAGE, whatsapp_message_id=wamid, is_internal=False, status="sent"
            )
            db.add(corp_msg)
            conv.automation_paused = True
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(corp_msg)
            await ws_manager.broadcast_to_branch(conv.branch_id, {
                "type": "new_incoming_message",
                "conversation_id": conv.id,
                "branch_id": conv.branch_id,
                "contact_name": contact.name,
                "contact_phone": contact.phone,
                "message": {
                    "id": corp_msg.id, "direction": corp_msg.direction, "sender_type": corp_msg.sender_type,
                    "content": corp_msg.content, "status": corp_msg.status, "created_at": corp_msg.created_at.isoformat()
                },
                "is_new_conversation": False
            })
            return

        # 3.2 Actualizar delivery_type si se seleccionó opción 1, 2 o 3
        if main_option_matched in ["visit", "delivery", "pickup"]:
            conv.delivery_type = main_option_matched
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

        # 4. Detección de sucursal si el cliente seleccionó una sucursal
        branch_matched_now = False
        matched_via = None
        matched_branch = None
        if interactive_id.startswith("branch_"):
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

        if matched_branch and (conv.branch_id != matched_branch.id or branch_matched_now):
            motivo = "el cliente tocó el menú de sucursales" if matched_via == "interactive" else "el cliente escribió el nombre de la sucursal"
            await _assign_conversation_branch(db, conv, matched_branch, motivo)
            branch_matched_now = True

        # 5. Si se seleccionó o tiene delivery_type (visit, delivery, pickup) pero falta sucursal:
        if conv.delivery_type in ["visit", "delivery", "pickup"] and conv.branch_id is None:
            if conv.delivery_type == "visit":
                prompt_text = BRANCH_SELECTION_VISIT_BODY
            elif conv.delivery_type == "delivery":
                prompt_text = BRANCH_SELECTION_DELIVERY_BODY
            else:
                prompt_text = BRANCH_SELECTION_PICKUP_BODY
            await _send_branch_selection_menu(db, wa_service, conv, contact, phone, prompt_body=prompt_text)
            return

        # 6. Si la sucursal fue elegida en este turno o acaba de completar delivery_type + sucursal:
        if (branch_matched_now or (main_option_matched in ["visit", "delivery", "pickup"] and conv.branch_id is not None)):
            await _send_branch_welcome_and_menu(db, wa_service, conv, contact, phone)
            return

        # 7. Si no tiene delivery_type y no tiene sucursal, mostrar el Menú Principal inicial de 4 opciones:
        if conv.delivery_type is None and conv.branch_id is None:
            now = datetime.now(timezone.utc)
            should_prompt = True
            if conv.last_branch_prompt_at:
                elapsed = (now - conv.last_branch_prompt_at.replace(tzinfo=timezone.utc)).total_seconds() if conv.last_branch_prompt_at.tzinfo else (datetime.utcnow() - conv.last_branch_prompt_at).total_seconds()
                if elapsed < 180 and not (message_type == "text" and any(k in text.lower() for k in ["hola", "menu", "opciones", "buenas", "ayuda", "1", "2", "3", "4"])):
                    should_prompt = False

            if should_prompt:
                await _send_main_welcome_menu(db, wa_service, conv, contact, phone)
                conv.last_branch_prompt_at = now
                db.commit()
            return

        # 8. Flujo de selección de pago (si aplica dentro del chat)
        if conv.branch_id is not None and conv.delivery_type in ["delivery", "pickup"] and conv.payment_method is None:
            matched_payment = None
            if interactive_id.startswith("pay_"):
                matched_payment = interactive_id.replace("pay_", "")
            elif message_type == "text":
                matched_payment = match_payment_method_text(text)

            if matched_payment:
                conv.payment_method = matched_payment
                conv.updated_at = datetime.now(timezone.utc)
                db.commit()

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
                conv.automation_paused = True
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
                return
    except Exception as e:
        logger.error(f"[AutoResponse Background Error] Conv ID {conv_id}: {e}", exc_info=True)
    finally:
        db.close()

def _send_push_notification_background(branch_id: Optional[int], conversation_id: int, contact_label: str, branch_label: str, message_preview: str):
    """
    Envía notificaciones push del navegador a los encargados de la sucursal (y supervisores/admins)
    cuando llega un mensaje nuevo. Se ejecuta desacoplado del ciclo de respuesta HTTP a Meta.
    branch_id puede ser None (conversación todavía sin sucursal asignada): en ese caso solo
    notifica a admin/supervisor, ver notify_branch_new_message.
    """
    db = SessionLocal()
    try:
        notify_branch_new_message(
            db=db,
            branch_id=branch_id,
            title=f"💬 {contact_label} • {branch_label}",
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

        # 7. Descarga rápida de archivos multimedia (inline) para que el mensaje nazca ya con su imagen
        media_url = None
        media_mime = msg_data.get("media_mime_type")
        if message_type != "text" and msg_data.get("media_id"):
            try:
                media_res = await asyncio.wait_for(
                    wa_service.download_media(msg_data["media_id"]),
                    timeout=3.5
                )
                if media_res:
                    media_url = save_media_bytes(media_res["bytes"], media_res["mime_type"])
                    media_mime = media_res["mime_type"]
                    logger.info(f"[FastMedia] Media descargado inline para WAMID {wamid}: {media_url}")
            except Exception as me:
                logger.warning(f"[FastMedia] Descarga inline no completada (se completará en background): {me}")

        # 8. Insertar mensaje entrante de forma atómica
        message = Message(
            conversation_id=conv.id,
            direction="incoming",
            sender_type="customer",
            content=text,
            whatsapp_message_id=wamid,
            is_internal=False,
            status="delivered",
            media_type=message_type if message_type != "text" else None,
            media_id=msg_data.get("media_id") if message_type != "text" else None,
            media_url=media_url,
            media_mime_type=media_mime,
            created_at=now
        )
        db.add(message)
        conv.updated_at = now
        db.commit()
        db.refresh(message)
        db.refresh(conv)

        # 9. Difundir evento de nuevo mensaje inmediatamente a agentes por WebSocket con media_url
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
                "media_url": message.media_url,
                "media_type": message.media_type,
                "media_mime_type": message.media_mime_type,
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

        # 10. Notificación push: a los agentes de la sucursal (si ya tiene una asignada) y
        # siempre a admin/supervisor, incluso si la conversación todavía no tiene sucursal.
        contact_label = contact.name or contact.phone
        branch_label = conv.branch.name if conv.branch_id and conv.branch else "Sin sucursal"
        background_tasks.add_task(
            _send_push_notification_background,
            branch_id=conv.branch_id,
            conversation_id=conv.id,
            contact_label=contact_label,
            branch_label=branch_label,
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
