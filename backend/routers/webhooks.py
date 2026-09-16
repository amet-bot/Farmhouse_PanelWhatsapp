import asyncio
import hmac
import hashlib
import json
import logging
import re
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
    MAIN_MENU_LIST_BUTTON, MAIN_MENU_LIST_ROWS, NAV_RESTART_ROW,
    BRANCH_SELECTION_BODY, BRANCH_SELECTION_VISIT_BODY, BRANCH_SELECTION_DELIVERY_BODY,
    BRANCH_SELECTION_PICKUP_BODY, BRANCH_SELECTION_BUTTON, BRANCH_SELECTION_MENU_DIRECT_BODY,
    CORPORATE_INTAKE_ENABLED, CORPORATE_CATERING_HANDOFF, CATERING_PHONE_DISPLAY,
    CORPORATE_INTAKE_INTRO, CORPORATE_EVENT_TYPE_QUESTION, CORPORATE_EVENT_TYPE_BUTTONS,
    CORPORATE_EVENT_TYPE_LABELS, CORPORATE_HEADCOUNT_QUESTION, CORPORATE_HEADCOUNT_RETRY,
    CORPORATE_HEADCOUNT_RANGE_ROWS, CORPORATE_HEADCOUNT_RANGE_LABELS,
    CORPORATE_DATE_QUESTION, CORPORATE_DATE_RETRY, CORPORATE_LOCATION_QUESTION,
    CORPORATE_LOCATION_QUESTION_AFTER_COMBINED_ANSWER,
    CORPORATE_DATE_QUICK_ROWS, CORPORATE_DATE_QUICK_LABELS,
    CORPORATE_LOCATION_BUTTONS, CORPORATE_LOCATION_LABELS, CORPORATE_INVALID_OPTION_RETRY,
    CORPORATE_INTAKE_CLOSING_MESSAGE, get_corporate_intake_summary,
    MANAGER_HELP_QUESTION, MANAGER_HELP_BUTTONS,
    get_main_welcome_body, get_branch_visit_message, get_branch_pickup_info_message,
    get_branch_delivery_info_message, MENU_LINK_WARM_CLOSING, get_manager_assigned_message,
    get_manager_declined_message,
    ACH_PAYMENT_INSTRUCTIONS, CARD_PAYMENT_MESSAGE, YAPPY_PAYMENT_MESSAGE,
    UNKNOWN_MAIN_MESSAGE, UNKNOWN_BRANCH_MESSAGE,
    AFTER_MENU_HELP_QUESTION, AFTER_MENU_HELP_BUTTONS, CHAT_ORDER_ROW,
    CHAT_ORDER_INTRO_QUESTION, CHAT_ORDER_PAYMENT_QUESTION, CHAT_ORDER_PAYMENT_ROWS,
    RESTART_MESSAGE, CANCEL_MESSAGE,
    CHANGE_ORDER_TYPE_MESSAGE, CHANGE_BRANCH_MESSAGE, get_human_handoff_message,
    get_customer_first_name
)
from services.media_storage import save_media_bytes, MEDIA_DOWNLOAD_FAILED_MARKER
from services.branch_matcher import match_branch_by_text
from services.order_flow_matcher import (
    match_main_option, match_delivery_type_text, match_payment_method_text,
    match_manager_help, match_event_type, match_event_location,
    match_entry_intent, match_navigation_intent
)
from services.push_service import notify_branch_new_message
from services.flow_content import get_node_text, get_node_options
from services import flow_engine
from security.auth import create_menu_session_token

logger = logging.getLogger("farmhouse.webhooks")

router = APIRouter(prefix="/webhooks", tags=["Webhooks Meta WhatsApp"])

# Pausa breve entre las burbujas de un mismo turno del bot (una vez que ya "empezó a escribir"),
# distinta de settings.BOT_RESPONSE_DELAY_SECONDS que es la pausa inicial antes de la primera respuesta.
BUBBLE_PACE_DELAY_SECONDS = 0.4

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
    # Una sola difusión a la unión de ambas sucursales: la anterior (para que retire la
    # conversación de su bandeja) y la nueva (para que la reciba). Emitir una vez por
    # sucursal entregaba el evento dos veces a los admins y supervisores globales.
    await ws_manager.broadcast_to_branches({old_branch_id, branch.id}, {
        "type": "conversation_transferred",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id
    })

# Bloque repetido en cada _send_* que manda algo por WhatsApp: llamar a la API, sacar el
# wamid de la respuesta, guardar el Message saliente y difundirlo al panel por WebSocket.
# `send_awaitable` es la llamada ya construida (ej. wa_service.send_text_message(phone, texto))
# para que cada _send_* elija el tipo de mensaje; `content` es lo que se guarda en el
# historial, que a veces difiere de lo que WhatsApp muestra (ej. un botón CTA no repite el
# link como texto, pero igual se guarda para que el panel lo pueda ver/copiar).
# Solo cubre el caso is_internal=False (un mensaje que sí llegó al cliente): las 2 notas
# internas del código (resumen de handoff, cierre corporativo) quedan aparte a propósito,
# porque ya difieren entre sí en si incluyen "is_internal" en el payload del WebSocket —
# unificarlas aquí forzaría a elegir cuál de las dos formas es la "correcta", que es una
# decisión de contrato con el frontend y no un refactor mecánico.
async def _send_and_log(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, send_awaitable, content: str) -> Message:
    send_res = await send_awaitable
    wamid = None
    if isinstance(send_res, dict) and "messages" in send_res and send_res["messages"]:
        wamid = send_res["messages"][0].get("id")
    msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=content, whatsapp_message_id=wamid, is_internal=False, status="sent"
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
    return msg

async def _send_main_welcome_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía el menú principal de bienvenida de Farmhouse como lista interactiva (Delivery,
    Retiro, Evento/empresa, Ver sucursales, Hablar con alguien), personalizado con el nombre
    de WhatsApp del cliente cuando se conoce. Una lista en vez de 3 botones evita el paso
    intermedio de "Hacer un pedido" -> submenú de tipo de entrega."""
    welcome_body = get_main_welcome_body(contact.name, db=db)
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    await _send_and_log(
        db, wa_service, conv, contact, phone,
        wa_service.send_interactive_list(phone, welcome_body, MAIN_MENU_LIST_BUTTON, MAIN_MENU_LIST_ROWS, section_title="¿Qué te gustaría hacer?"),
        welcome_body,
    )
    logger.info(f"[MainMenu] Menú principal enviado a {mask_phone(phone)} para Conv ID {conv.id}.")

async def _send_unknown_main_prompt(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Recuperación contextual cuando no hay nada más específico que ofrecer: mismo menú
    principal de siempre, con un texto que reconoce que no se entendió bien."""
    await _send_interactive_list_message(
        db, wa_service, conv, contact, phone,
        get_node_text(db, "unknown_main_message", UNKNOWN_MAIN_MESSAGE),
        MAIN_MENU_LIST_BUTTON, MAIN_MENU_LIST_ROWS, section_title="¿Qué te gustaría hacer?",
    )

async def _reset_bot_context(db: Session, conv: Conversation, *, clear_branch: bool = True) -> None:
    """Reinicia únicamente el contexto del bot; no elimina mensajes ni pedidos históricos."""
    conv.delivery_type = None
    conv.payment_method = None
    conv.corporate_intake_step = None
    conv.corporate_intake_notes = None
    conv.awaiting_chat_order_description = None
    if clear_branch:
        conv.branch_id = None
        conv.assigned_user_id = None
        conv.status = "unassigned"
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()


# Mapa clave corta -> (id del nodo editable, texto de respaldo). Las claves coinciden a
# propósito con los valores de conv.delivery_type ("visit"/"delivery"/"pickup") para que los
# call sites puedan pasar prompt_key=conv.delivery_type directamente.
_BRANCH_PROMPT_NODES = {
    "visit": ("branch_selection_visit_body", BRANCH_SELECTION_VISIT_BODY),
    "delivery": ("branch_selection_delivery_body", BRANCH_SELECTION_DELIVERY_BODY),
    "pickup": ("branch_selection_pickup_body", BRANCH_SELECTION_PICKUP_BODY),
    "menu_direct": ("branch_selection_menu_direct_body", BRANCH_SELECTION_MENU_DIRECT_BODY),
}

async def _send_branch_selection_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, intro_text: Optional[str] = None, prompt_body: Optional[str] = None, prompt_key: Optional[str] = None) -> None:
    """Envía (opcionalmente) un mensaje introductorio y luego la lista interactiva de sucursales activas.
    `prompt_key` resuelve el cuerpo del mensaje contra el nodo editable correspondiente (ver
    _BRANCH_PROMPT_NODES); `prompt_body` sigue existiendo para un texto ya resuelto/puntual."""
    if prompt_key and prompt_key in _BRANCH_PROMPT_NODES:
        node_id, fallback = _BRANCH_PROMPT_NODES[prompt_key]
        body_text = get_node_text(db, node_id, fallback)
    else:
        body_text = prompt_body or BRANCH_SELECTION_BODY
    if intro_text:
        await asyncio.sleep(0.3)
        await _send_and_log(db, wa_service, conv, contact, phone, wa_service.send_text_message(phone, intro_text), intro_text)

    await asyncio.sleep(0.3)
    active_branches = db.query(Branch).filter(Branch.active == True).order_by(Branch.name).all()
    if not active_branches:
        return
    # Se topa a 9 sucursales (no 10) para dejarle siempre un lugar a la fila de "empezar de
    # nuevo" sin violar el máximo de 10 filas que permite Meta.
    rows = [{"id": f"branch_{b.id}", "title": b.name[:24], "description": f"Sucursal {b.name}"[:72]} for b in active_branches if b.code != "CAT"][:9]
    if not rows:
        rows = [{"id": f"branch_{b.id}", "title": b.name[:24]} for b in active_branches[:9]]
    rows = rows + [NAV_RESTART_ROW]

    await _send_and_log(
        db, wa_service, conv, contact, phone,
        wa_service.send_interactive_list(phone, body_text, BRANCH_SELECTION_BUTTON, rows),
        f"📋 {body_text}",
    )
    logger.info(f"[BranchSelection] Menú de {len(rows)} sucursales enviado a {mask_phone(phone)} para Conv ID {conv.id}.")

async def _send_interactive_list_message(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, body_text: str, button_text: str, rows: list, section_title: str = "Opciones") -> None:
    """Envía un mensaje con una lista interactiva (hasta 10 filas, con descripción opcional por
    fila) y lo registra/difunde. Toda pregunta del bot usa listas en vez de los 3 botones
    simples que permite Meta, para poder incluir siempre una fila de "empezar de nuevo" sin
    sacrificar una opción real."""
    await _send_and_log(
        db, wa_service, conv, contact, phone,
        wa_service.send_interactive_list(phone, body_text, button_text, rows, section_title=section_title),
        body_text,
    )

def _manager_help_rows(db: Session) -> list:
    titles = get_node_options(db, "manager_help_question", [b["title"] for b in MANAGER_HELP_BUTTONS])
    rows = [{"id": b["id"], "title": t} for b, t in zip(MANAGER_HELP_BUTTONS, titles)]
    return rows + [NAV_RESTART_ROW]

async def _send_manager_help_prompt(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía el prompt de '¿Algo más?' (hablar con gerente / ver el menú / despedida / empezar
    de nuevo) tras la info de sucursal, como lista tocable."""
    question_text = get_node_text(db, "manager_help_question", MANAGER_HELP_QUESTION)
    await _send_interactive_list_message(
        db, wa_service, conv, contact, phone, question_text, "Elegir opción",
        _manager_help_rows(db), section_title="¿Algo más?",
    )

async def _send_digital_menu_link(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía el enlace personalizado al Menú Digital (/menu), con copy según el tipo de atención."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    branch_code = conv.branch.code if conv.branch else ""
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
        fallback_body = (
            f"🍽️ Aquí tienes nuestro Menú Digital para armar tu pedido a domicilio desde Farmhouse *{{sucursal}}*.\n\n"
            f"_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos, ingresa tu dirección y envíanos tu orden en 1 clic._\n\n"
            f"{MENU_LINK_WARM_CLOSING}"
        )
        body_text = get_node_text(db, "menu_link_delivery_body", fallback_body, sucursal=branch_name)
        button_text = "Ver menú y pedir"
    elif conv.delivery_type == "pickup":
        fallback_body = (
            f"🍽️ Échale un vistazo a nuestro Menú Digital y arma tu pedido para retirar en Farmhouse *{{sucursal}}*.\n\n"
            f"_Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos y te lo tendremos fresco y listo cuando pases a retirarlo._\n\n"
            f"{MENU_LINK_WARM_CLOSING}"
        )
        body_text = get_node_text(db, "menu_link_pickup_body", fallback_body, sucursal=branch_name)
        button_text = "Ver menú y pedir"
    else:
        fallback_body = (
            f"🍽️ Aquí tienes nuestro Menú Digital de Farmhouse *{{sucursal}}*.\n\n"
            f"_Así vas viendo qué se te antoja antes de llegar, o si prefieres, también puedes hacer tu pedido desde aquí mismo._"
        )
        body_text = get_node_text(db, "menu_link_generic_body", fallback_body, sucursal=branch_name)
        button_text = "Ver menú"

    # El botón CTA no muestra el link como texto en WhatsApp, pero se guarda igual en el
    # registro interno para que el panel del agente pueda verlo/copiarlo si hace falta.
    stored_content = f"{body_text}\n\n[Botón: {button_text}] → {menu_url}"
    await _send_and_log(db, wa_service, conv, contact, phone, wa_service.send_cta_url_message(phone, body_text, button_text, menu_url), stored_content)

async def _send_plain_text_message(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, text: str) -> None:
    """Envía un mensaje de texto plano (ej. dirección/horario/maps de una sucursal), lo guarda y lo difunde por WebSocket."""
    await _send_and_log(db, wa_service, conv, contact, phone, wa_service.send_text_message(phone, text), text)

def _conversation_context_summary(conv: Conversation) -> str:
    """Resumen breve para el equipo cuando el cliente pide atención humana."""
    delivery_labels = {"visit": "Consulta/visita", "delivery": "Delivery", "pickup": "Retiro"}
    payment_labels = {"card": "Tarjeta", "yappy": "Yappy", "cash": "Efectivo", "ach": "ACH"}
    lines = ["📋 Contexto recopilado por el asistente:"]
    lines.append(f"• Necesidad: {delivery_labels.get(conv.delivery_type, 'Aún no definida')}")
    lines.append(f"• Sucursal: {conv.branch.name if conv.branch else 'Aún no definida'}")
    if conv.payment_method:
        lines.append(f"• Pago: {payment_labels.get(conv.payment_method, conv.payment_method)}")
    if conv.corporate_intake_notes:
        lines.append(f"• Evento/empresa:\n{conv.corporate_intake_notes}")
    return "\n".join(lines)

async def _handoff_to_human(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, customer_text: Optional[str] = None) -> None:
    """Responde, deja contexto interno y pausa el bot sin hacer repetir al cliente."""
    branch_name = conv.branch.name if conv.branch else None
    await _send_plain_text_message(
        db, wa_service, conv, contact, phone,
        customer_text or get_human_handoff_message(branch_name, db=db),
    )
    summary_msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        sender_type="system",
        content=_conversation_context_summary(conv),
        is_internal=True,
        status="sent",
    )
    db.add(summary_msg)
    conv.automation_paused = True
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(summary_msg)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": summary_msg.id, "direction": summary_msg.direction,
            "sender_type": summary_msg.sender_type, "content": summary_msg.content,
            "status": summary_msg.status, "created_at": summary_msg.created_at.isoformat(),
            "is_internal": True,
        },
        "is_new_conversation": False,
    })

def _last_public_bot_text(db: Session, conv_id: int) -> str:
    msg = db.query(Message).filter(
        Message.conversation_id == conv_id,
        Message.direction == "outgoing",
        Message.is_internal == False,
    ).order_by(Message.created_at.desc(), Message.id.desc()).first()
    return msg.content if msg else ""

async def _send_branch_welcome_and_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Envía la información de la sucursal (visita, retiro o domicilio) y/o el enlace del Menú Digital según el tipo de atención."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    branch_code = conv.branch.code if conv.branch else ""

    if conv.delivery_type == "visit":
        visit_text = get_branch_visit_message(branch_code, branch_name, db=db)
        await _send_plain_text_message(db, wa_service, conv, contact, phone, visit_text)
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_manager_help_prompt(db, wa_service, conv, contact, phone)
        return

    if conv.delivery_type == "pickup":
        pickup_info_text = get_branch_pickup_info_message(branch_code, branch_name, db=db)
        await _send_plain_text_message(db, wa_service, conv, contact, phone, pickup_info_text)
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_digital_menu_link(db, wa_service, conv, contact, phone)
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_after_menu_help_prompt(db, wa_service, conv, contact, phone)
        return

    if conv.delivery_type == "delivery":
        delivery_info_text = get_branch_delivery_info_message(branch_code, branch_name, db=db)
        await _send_plain_text_message(db, wa_service, conv, contact, phone, delivery_info_text)
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_digital_menu_link(db, wa_service, conv, contact, phone)
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_after_menu_help_prompt(db, wa_service, conv, contact, phone)
        return

    # Caso genérico (no debería alcanzarse: delivery_type siempre es visit/pickup/delivery aquí)
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    await _send_digital_menu_link(db, wa_service, conv, contact, phone)
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    await _send_after_menu_help_prompt(db, wa_service, conv, contact, phone)

def _after_menu_help_rows(db: Session) -> list:
    titles = get_node_options(db, "after_menu_help_question", [b["title"] for b in AFTER_MENU_HELP_BUTTONS])
    rows = [{"id": b["id"], "title": t} for b, t in zip(AFTER_MENU_HELP_BUTTONS, titles)]
    return rows + [CHAT_ORDER_ROW, NAV_RESTART_ROW]

async def _send_after_menu_help_prompt(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Lista de '¿algo más?' tras mandar el link del Menú Digital: abrir el menú de nuevo,
    cambiar sucursal, hablar con alguien, pedir y pagar por chat, o empezar de nuevo. Se manda
    en el mismo turno que el link (no solo como recuperación en el turno siguiente) para que
    nunca quede un mensaje sin ninguna opción tocable."""
    question_text = get_node_text(db, "after_menu_help_question", AFTER_MENU_HELP_QUESTION)
    await _send_interactive_list_message(
        db, wa_service, conv, contact, phone, question_text, "Elegir opción",
        _after_menu_help_rows(db), section_title="¿Algo más?",
    )

def _append_corporate_note(conv: Conversation, line: str) -> None:
    """Acumula una línea más al resumen de respuestas del flujo Corporativo/Evento."""
    conv.corporate_intake_notes = f"{conv.corporate_intake_notes}\n{line}" if conv.corporate_intake_notes else line

def _corporate_event_type_buttons(db: Session) -> list:
    titles = get_node_options(db, "corporate_event_type_question", [b["title"] for b in CORPORATE_EVENT_TYPE_BUTTONS])
    return [{"id": b["id"], "title": t} for b, t in zip(CORPORATE_EVENT_TYPE_BUTTONS, titles)]

def _corporate_location_buttons(db: Session) -> list:
    titles = get_node_options(db, "corporate_location_question", [b["title"] for b in CORPORATE_LOCATION_BUTTONS])
    return [{"id": b["id"], "title": t} for b, t in zip(CORPORATE_LOCATION_BUTTONS, titles)]

async def _render_corporate_node(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, node_id: str) -> None:
    """Manda el mensaje real de uno de los 5 nodos 'ejecutables' del intake corporativo (ver
    _CORPORATE_NODE_RENDER). Centraliza el mapeo tipo->envío para que tanto el avance normal
    (_advance_corporate_step) como un retroceso ("volver") rendericen exactamente igual."""
    fallback_text, buttons_kind = _CORPORATE_NODE_RENDER[node_id]
    text = get_node_text(db, node_id, fallback_text)
    if buttons_kind == "event_type":
        rows = _corporate_event_type_buttons(db) + [NAV_RESTART_ROW]
        await _send_interactive_list_message(db, wa_service, conv, contact, phone, text, "Elegir opción", rows, section_title="¿Qué tipo de evento?")
    elif buttons_kind == "location":
        rows = _corporate_location_buttons(db) + [NAV_RESTART_ROW]
        await _send_interactive_list_message(db, wa_service, conv, contact, phone, text, "Elegir opción", rows, section_title="¿Dónde lo recibes?")
    elif buttons_kind == "headcount":
        rows = list(CORPORATE_HEADCOUNT_RANGE_ROWS) + [NAV_RESTART_ROW]
        await _send_interactive_list_message(db, wa_service, conv, contact, phone, text, "Elegir opción", rows, section_title="¿Para cuántas personas?")
    elif buttons_kind == "date":
        rows = list(CORPORATE_DATE_QUICK_ROWS) + [NAV_RESTART_ROW]
        await _send_interactive_list_message(db, wa_service, conv, contact, phone, text, "Elegir opción", rows, section_title="¿Cuándo sería?")
    else:
        await _send_plain_text_message(db, wa_service, conv, contact, phone, text)

async def _send_corporate_event_type_question(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    await _render_corporate_node(db, wa_service, conv, contact, phone, "corporate_event_type_question")

async def _send_corporate_location_question(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    await _render_corporate_node(db, wa_service, conv, contact, phone, "corporate_location_question")

# Único tramo del bot donde las CONEXIONES del grafo `main_intake` (services/flow_engine.py)
# deciden de verdad qué paso sigue, no solo el texto (ver docstring de models/bot_flow.py).
# Nodo -> (constante de respaldo del texto, tipo de lista tocable que acompaña el texto).
# `None` en el segundo valor es mensaje de texto plano. Solo estos 5 nodos son "ejecutables"
# por el motor; cualquier otro destino (el admin borró/renombró el nodo, o conectó algo fuera
# de este sub-flujo) se ignora y cae al respaldo indicado por el caller — ver _advance_corporate_step.
_CORPORATE_NODE_RENDER = {
    "corporate_event_type_question": (CORPORATE_EVENT_TYPE_QUESTION, "event_type"),
    "corporate_headcount_question": (CORPORATE_HEADCOUNT_QUESTION, "headcount"),
    "corporate_date_question": (CORPORATE_DATE_QUESTION, "date"),
    "corporate_location_question": (CORPORATE_LOCATION_QUESTION, "location"),
    "corporate_location_after_combined": (CORPORATE_LOCATION_QUESTION_AFTER_COMBINED_ANSWER, "location"),
}
_CORPORATE_NODE_TO_STEP = {
    "corporate_event_type_question": 1,
    "corporate_headcount_question": 2,
    "corporate_date_question": 3,
    "corporate_location_question": 4,
    "corporate_location_after_combined": 4,
}


async def _finish_corporate_intake(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Cierra el intake corporativo: mensaje de cierre al cliente, resumen interno para Sol
    (visible solo en el panel) y pausa del bot. Se llega aquí tras las 4 preguntas en orden,
    o antes si el admin reconectó algún paso directo al nodo 'corporate_closing' en el editor."""
    conv.corporate_intake_step = None
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()

    await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_closing", CORPORATE_INTAKE_CLOSING_MESSAGE))

    summary_msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="system",
        content=get_corporate_intake_summary(conv.corporate_intake_notes or ""),
        is_internal=True, status="sent"
    )
    db.add(summary_msg)
    conv.automation_paused = True
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(summary_msg)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message",
        "conversation_id": conv.id,
        "branch_id": conv.branch_id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "message": {
            "id": summary_msg.id, "direction": summary_msg.direction, "sender_type": summary_msg.sender_type,
            "content": summary_msg.content, "status": summary_msg.status, "created_at": summary_msg.created_at.isoformat()
        },
        "is_new_conversation": False
    })


async def _advance_corporate_step(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, from_node_id: str, port: int, fallback_node_id: str) -> None:
    """
    Avanza el intake corporativo desde `from_node_id`: le pregunta al grafo `main_intake` cuál
    es el nodo conectado en (`from_node_id`, `port`) y renderiza ESE (ver
    services/flow_engine.py). Si el grafo no tiene esa conexión, está roto, o el nodo destino
    ya no es uno de los 5 reconocidos en `_CORPORATE_NODE_RENDER`, usa `fallback_node_id` — el
    comportamiento de siempre.

    Toda la VALIDACIÓN de la respuesta del cliente (¿qué botón tocó? ¿el texto trae fecha?) ya
    ocurrió en el caller (_handle_corporate_intake_step); esta función solo decide y manda el
    siguiente paso.
    """
    next_node_id = flow_engine.get_next_node_id(db, from_node_id, port, fallback_node_id)
    if next_node_id not in _CORPORATE_NODE_TO_STEP and next_node_id != "corporate_closing":
        next_node_id = fallback_node_id

    if next_node_id == "corporate_closing":
        await _finish_corporate_intake(db, wa_service, conv, contact, phone)
        return

    conv.corporate_intake_step = _CORPORATE_NODE_TO_STEP[next_node_id]
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()

    await _render_corporate_node(db, wa_service, conv, contact, phone, next_node_id)


async def _handle_corporate_intake_step(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, interactive_id: str, message_type: str, text: str) -> None:
    """Procesa la respuesta del cliente a una de las 4 preguntas guiadas del Pedido
    Corporativo/Evento (opción 4), antes de pasarle la conversación a Sol."""
    step = conv.corporate_intake_step

    if step == 1:
        event_type = None
        if interactive_id == "event_type_meeting":
            event_type = "meeting"
        elif interactive_id == "event_type_celebration":
            event_type = "celebration"
        elif interactive_id == "event_type_other":
            event_type = "other"
        elif message_type == "text":
            event_type = match_event_type(text)

        if not event_type:
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_invalid_option_retry", CORPORATE_INVALID_OPTION_RETRY))
            await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
            await _send_corporate_event_type_question(db, wa_service, conv, contact, phone)
            return

        _append_corporate_note(conv, f"Tipo de evento: {CORPORATE_EVENT_TYPE_LABELS[event_type]}")
        await _advance_corporate_step(db, wa_service, conv, contact, phone, "corporate_event_type_question", 0, "corporate_headcount_question")
        return

    if step == 2:
        if interactive_id == "hc_type_exact":
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_headcount_question", CORPORATE_HEADCOUNT_QUESTION))
            return
        range_label = CORPORATE_HEADCOUNT_RANGE_LABELS.get(interactive_id)
        if range_label:
            answer = range_label
        elif message_type == "text" and text.strip():
            answer = text.strip()
        else:
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_headcount_retry", CORPORATE_HEADCOUNT_RETRY))
            return
        _append_corporate_note(conv, f"Cantidad de personas: {answer}")

        # Si la persona respondió cantidad y fecha en la misma frase, aprovechamos esa
        # información y evitamos una pregunta redundante. Si solo dio la cantidad, el flujo
        # clásico sigue funcionando y pregunta por la fecha a continuación.
        normalized_answer = answer.lower()
        date_words = (
            "lunes", "martes", "miércoles", "miercoles", "jueves", "viernes", "sábado", "sabado", "domingo",
            "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
            "octubre", "noviembre", "diciembre", "mañana", "manana", "hoy", "mediodía", "mediodia",
        )
        has_date_or_time = (
            any(word in normalized_answer for word in date_words)
            or bool(re.search(r"\b\d{1,2}[:/]\d{1,2}(?:[/]\d{2,4})?\b", normalized_answer))
            or bool(re.search(r"\b\d{1,2}\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)\b", normalized_answer))
        )
        if has_date_or_time:
            _append_corporate_note(conv, f"Fecha y hora (respuesta conjunta): {answer}")
        await _advance_corporate_step(
            db, wa_service, conv, contact, phone, "corporate_headcount_question",
            1 if has_date_or_time else 0,
            "corporate_location_after_combined" if has_date_or_time else "corporate_date_question",
        )
        return

    if step == 3:
        if interactive_id == "date_type_exact":
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_date_question", CORPORATE_DATE_QUESTION))
            return
        quick_label = CORPORATE_DATE_QUICK_LABELS.get(interactive_id)
        if quick_label:
            answer = quick_label
        elif message_type == "text" and text.strip():
            answer = text.strip()
        else:
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_date_retry", CORPORATE_DATE_RETRY))
            return
        _append_corporate_note(conv, f"Fecha y hora: {answer}")
        await _advance_corporate_step(db, wa_service, conv, contact, phone, "corporate_date_question", 0, "corporate_location_question")
        return

    if step == 4:
        location = None
        if interactive_id == "event_loc_pickup":
            location = "pickup"
        elif interactive_id == "event_loc_delivery":
            location = "delivery"
        elif interactive_id == "event_loc_undecided":
            location = "undecided"
        elif message_type == "text":
            location = match_event_location(text)

        if not location:
            await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_invalid_option_retry", CORPORATE_INVALID_OPTION_RETRY))
            await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
            await _send_corporate_location_question(db, wa_service, conv, contact, phone)
            return

        _append_corporate_note(conv, f"Lugar de entrega: {CORPORATE_LOCATION_LABELS[location]}")
        location_port = {"pickup": 0, "delivery": 1, "undecided": 2}[location]
        await _advance_corporate_step(db, wa_service, conv, contact, phone, "corporate_location_question", location_port, "corporate_closing")
        return

# --- Pasos de _process_auto_flow_background, en el mismo orden en que se llaman ahí abajo ---
#
# Cada función de aquí es el cuerpo de un bloque numerado que antes vivía inline (ver
# comentarios "0.", "1.", "2." ... dentro de _process_auto_flow_background). Se movieron tal
# cual, sin cambiar su lógica ni el orden en que se evalúan sus condiciones — el objetivo es
# solo que la secuencia de decisiones del bot quede visible en un solo lugar en vez de
# enterrada en una función de cientos de líneas. Los bloques de 1-3 líneas (un solo
# _send_*/return, o una condición trivial) se dejaron inline a propósito: envolverlos en una
# función no habría hecho más legible nada.

async def _step_confirm_web_menu_order(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, text: str) -> None:
    """Bloque 0: pedido estructurado enviado desde la Web App de Menú (/menu). Ya trae
    sucursal, entrega y pago resueltos (ver POST /api/orders/public); responde con un mensaje
    cálido y empático según el tipo de entrega, y pausa el bot para que el agente de la
    sucursal tome el control personal de la conversación."""
    is_delivery = "DELIVERY" in text.upper() or (conv.delivery_type == "delivery")
    is_card = "TARJETA" in text.upper() or (conv.payment_method == "card")
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    first_name = get_customer_first_name(contact.name)
    greeting = f"¡Gracias por tu pedido, {first_name}!" if first_name else "¡Gracias por tu pedido!"
    payment_labels = {"card": "Tarjeta", "yappy": "Yappy", "ach": "ACH / transferencia", "cash": "Efectivo"}
    payment_label = payment_labels.get(conv.payment_method, "Por confirmar")
    delivery_label = "Delivery" if is_delivery else "Retiro en sucursal"
    next_step = (
        "El equipo revisará tu dirección, te confirmará el costo de entrega"
        + (" y te enviará el enlace de pago seguro." if is_card else " y coordinará el pago contigo.")
        if is_delivery else
        ("El equipo te enviará el enlace de pago y confirmará cuándo estará listo."
         if is_card else "El equipo te confirmará cuándo estará listo para retirar.")
    )
    confirmation_text = (
        f"{greeting} Ya lo tenemos registrado 🌿\n\n"
        f"*Resumen*\n"
        f"• {delivery_label}\n"
        f"• Farmhouse {branch_name}\n"
        f"• Pago: {payment_label}\n\n"
        f"{next_step}\n\n"
        "Si ves algo que quieras corregir, escríbelo aquí; la persona que continúe contigo podrá ver todo este contexto."
    )

    # automation_paused se marca ANTES de mandar el mensaje, no después: _send_and_log
    # hace un solo commit con conv.updated_at, y al ya venir con automation_paused=True
    # en el mismo objeto conv, ambos cambios quedan en ese mismo commit — el estado final
    # es idéntico a cuando esto se escribía en un bloque aparte.
    conv.automation_paused = True
    await _send_plain_text_message(db, wa_service, conv, contact, phone, confirmation_text)

async def _step_download_pending_media(db: Session, wa_service, conv: Conversation, message_type: str, msg_data: Dict[str, Any], incoming_msg: Optional[Message]) -> None:
    """Bloque 1: descarga el archivo multimedia adjunto si existe y aún no fue descargado
    inline. Nunca corta el procesamiento (no hay ningún `return` aquí ni en el llamador para
    este bloque): solo actualiza el registro del mensaje y notifica al panel por WebSocket."""
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

async def _step_handle_restart_or_cancel(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, navigation_intent: Optional[str]) -> None:
    """Bloque 3 (interrupción universal): "reiniciar" o "cancelar" — limpia el contexto y
    vuelve a mostrar el menú principal, con el mensaje que corresponde a cuál de las dos fue."""
    await _reset_bot_context(db, conv)
    await _send_plain_text_message(
        db, wa_service, conv, contact, phone,
        get_node_text(db, "restart_message", RESTART_MESSAGE) if navigation_intent == "restart" else get_node_text(db, "cancel_message", CANCEL_MESSAGE),
    )
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    await _send_main_welcome_menu(db, wa_service, conv, contact, phone)

async def _step_handle_change_branch(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 3 (interrupción universal): "cambiar de sucursal" — limpia sucursal/agente/pago
    asignados y vuelve a preguntar sucursal (si ya había un tipo de entrega/visita elegido) o
    el menú principal completo (si no)."""
    conv.branch_id = None
    conv.assigned_user_id = None
    conv.payment_method = None
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "change_branch_message", CHANGE_BRANCH_MESSAGE))
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    if conv.delivery_type in ("visit", "delivery", "pickup"):
        await _send_branch_selection_menu(db, wa_service, conv, contact, phone, prompt_key=conv.delivery_type)
    else:
        await _send_main_welcome_menu(db, wa_service, conv, contact, phone)

async def _step_handle_change_order_type_or_back(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, navigation_intent: str, text: str, message_type: str) -> None:
    """Bloque 3 (interrupción universal): "cambiar tipo de pedido" o "volver". Dentro del
    intake corporativo, "volver" retrocede una pregunta conservando las respuestas anteriores;
    fuera de él vuelve a Delivery/Retiro/Evento. Tiene dos caminos de salida (uno dentro del
    `if` de corporativo, otro al final) — ambos terminaban en `return` en el bloque original,
    así que aquí basta con que la función termine (ver el único `return` en el llamador)."""
    if navigation_intent == "back" and conv.corporate_intake_step:
        if conv.corporate_intake_step <= 1:
            await _reset_bot_context(db, conv)
            await _send_main_welcome_menu(db, wa_service, conv, contact, phone)
        else:
            conv.corporate_intake_step -= 1
            note_lines = (conv.corporate_intake_notes or "").splitlines()
            conv.corporate_intake_notes = "\n".join(note_lines[:-1]) or None
            db.commit()
            previous_node_by_step = {
                1: "corporate_event_type_question",
                2: "corporate_headcount_question",
                3: "corporate_date_question",
            }
            await _send_plain_text_message(db, wa_service, conv, contact, phone, "Claro, corrijámoslo.")
            await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
            await _render_corporate_node(db, wa_service, conv, contact, phone, previous_node_by_step[conv.corporate_intake_step])
        return

    desired_type = match_delivery_type_text(text) if message_type == "text" else None
    await _reset_bot_context(db, conv)
    if desired_type:
        conv.delivery_type = desired_type
        db.commit()
        await _send_branch_selection_menu(db, wa_service, conv, contact, phone, prompt_key=desired_type)
    else:
        await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "change_order_type_message", CHANGE_ORDER_TYPE_MESSAGE))
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_main_welcome_menu(db, wa_service, conv, contact, phone)

async def _step_start_chat_order(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 2.55: "Pedir y pagar por chat" — pide describir el pedido en texto libre."""
    conv.awaiting_chat_order_description = True
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "chat_order_intro_question", CHAT_ORDER_INTRO_QUESTION))

async def _step_handle_chat_order_description(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 2.55 (continuación): ya llegó la descripción del pedido por chat, ahora se
    resuelve el método de pago con una lista tocable."""
    conv.awaiting_chat_order_description = False
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    payment_rows = list(CHAT_ORDER_PAYMENT_ROWS) + [NAV_RESTART_ROW]
    await _send_interactive_list_message(
        db, wa_service, conv, contact, phone,
        get_node_text(db, "chat_order_payment_question", CHAT_ORDER_PAYMENT_QUESTION),
        "Elegir opción", payment_rows, section_title="Método de pago",
    )

async def _step_handle_manager_help_yes(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 3.0: el cliente confirma que sí quiere ayuda adicional -> pasa a atención humana."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    ans_text = get_manager_assigned_message(branch_name, db=db)
    await _handoff_to_human(db, wa_service, conv, contact, phone, ans_text)

async def _step_handle_manager_help_no(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 3.0: el cliente dice que no necesita más ayuda."""
    branch_name = conv.branch.name if conv.branch else "Farmhouse"
    ans_text = get_manager_declined_message(branch_name, db=db)
    await _send_plain_text_message(db, wa_service, conv, contact, phone, ans_text)

async def _step_handle_manager_help_menu(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 3.0: el cliente pide ver el menú digital de nuevo."""
    await _send_digital_menu_link(db, wa_service, conv, contact, phone)
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
    await _send_manager_help_prompt(db, wa_service, conv, contact, phone)

async def _step_handle_corporate_option_selected(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str) -> None:
    """Bloque 3.1: opción "Pedido Corporativo / Evento". Por defecto el bot no pregunta nada:
    entrega de una el número del equipo de catering (ver CORPORATE_INTAKE_ENABLED en
    auto_responses.py). Con el interruptor en True hace las 4 preguntas guiadas antes de
    pasarle la conversación a Sol. Tiene dos caminos de salida (uno para cada valor de
    CORPORATE_INTAKE_ENABLED) — ambos terminaban en `return` en el bloque original, así que
    aquí basta con que la función termine (ver el único `return` en el llamador)."""
    cat_branch = db.query(Branch).filter((Branch.code == "CAT") | (Branch.name.ilike("%catering%"))).first()
    if cat_branch:
        await _assign_conversation_branch(db, conv, cat_branch, "cliente seleccionó Pedido Corporativo / Evento")

    if not CORPORATE_INTAKE_ENABLED:
        await _send_plain_text_message(
            db, wa_service, conv, contact, phone,
            get_node_text(db, "corporate_catering_handoff", CORPORATE_CATERING_HANDOFF),
        )
        # Nota interna (no le llega al cliente): deja registro en el panel de que esta
        # conversación era un prospecto de catering y a dónde se lo mandó, por si el
        # equipo quiere darle seguimiento desde aquí.
        db.add(Message(
            conversation_id=conv.id, direction="outgoing", sender_type="system",
            content=f"📋 Prospecto de catering: se le compartió el número del equipo de eventos ({CATERING_PHONE_DISPLAY}).",
            is_internal=True, status="sent",
        ))
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        return

    await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "corporate_intro", CORPORATE_INTAKE_INTRO))
    await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)

    conv.corporate_intake_step = 1
    conv.corporate_intake_notes = None
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()

    await _send_corporate_event_type_question(db, wa_service, conv, contact, phone)

async def _step_detect_and_assign_branch(db: Session, conv: Conversation, interactive_id: str, message_type: str, text: str) -> bool:
    """Bloque 4: detecta si el cliente seleccionó/mencionó una sucursal (por botón o por texto
    libre) y la asigna a la conversación. Devuelve `branch_matched_now`, que el llamador
    necesita para decidir el bloque 6."""
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

    return branch_matched_now

async def _step_prompt_entry_when_context_missing(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, message_type: str, text: str) -> None:
    """Bloque 7: todavía no hay ni tipo de entrega ni sucursal. Muestra el menú principal de
    nuevo, salvo que ya se mostró hace menos de 3 minutos y el cliente no escribió algo que
    sugiera que quiere retomar (en cuyo caso solo se reconoce el mensaje sin repetir el menú).
    Nunca se omite una respuesta por completo."""
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
    else:
        await _send_unknown_main_prompt(db, wa_service, conv, contact, phone)

async def _step_handle_payment_selection(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, interactive_id: str, message_type: str, text: str) -> bool:
    """Bloque 8: si ya hay sucursal + tipo de entrega pero falta método de pago, intenta
    resolverlo (por botón "pay_*" o por texto libre) y, si lo logra, cierra con el mensaje de
    ese método y pasa a atención humana. Devuelve True si resolvió el pago (el llamador debe
    `return` en ese caso); False si no hubo match, igual que el `if matched_payment:` original
    que no tenía un `else` y seguía al bloque siguiente."""
    matched_payment = None
    if interactive_id.startswith("pay_"):
        candidate = interactive_id.replace("pay_", "")
        matched_payment = candidate if candidate in {"ach", "card", "yappy"} else None
    elif message_type == "text":
        matched_payment = match_payment_method_text(text)

    if not matched_payment:
        return False

    conv.payment_method = matched_payment
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()

    payment_closing_messages = {
        "ach": get_node_text(db, "payment_ach", ACH_PAYMENT_INSTRUCTIONS),
        "card": get_node_text(db, "payment_card", CARD_PAYMENT_MESSAGE),
        "yappy": get_node_text(db, "payment_yappy", YAPPY_PAYMENT_MESSAGE),
    }
    closing_text = payment_closing_messages.get(
        conv.payment_method,
        "¡Genial! Ya tenemos todo listo para arrancar 😊 En un momento alguien de nuestro equipo te atiende para tomar los detalles de tu pedido. ¡Gracias por tu paciencia!"
    )
    await _handoff_to_human(db, wa_service, conv, contact, phone, closing_text)
    return True

async def _step_recover_conversation_context(db: Session, wa_service, conv: Conversation, contact: Contact, phone: str, text: str) -> None:
    """Bloque 9: recuperación contextual final. Todo mensaje que llega hasta aquí obtiene una
    salida útil: repetir la decisión pertinente, ofrecer acciones o volver al inicio, nunca
    quedarse en silencio. Es el último bloque de la función — sus varios `return` internos
    equivalían a terminar _process_auto_flow_background, así que aquí basta con que la función
    termine (ver el único `return` en el llamador, justo después de llamarla)."""
    if conv.delivery_type == "visit" and conv.branch_id is not None:
        await _send_plain_text_message(db, wa_service, conv, contact, phone, get_node_text(db, "visit_recovery_message", "Quiero asegurarme de ayudarte bien 😊"))
        await asyncio.sleep(BUBBLE_PACE_DELAY_SECONDS)
        await _send_manager_help_prompt(db, wa_service, conv, contact, phone)
        return

    if conv.delivery_type in ("delivery", "pickup") and conv.branch_id is not None:
        await _send_after_menu_help_prompt(db, wa_service, conv, contact, phone)
        return

    last_bot_text = _last_public_bot_text(db, conv.id)
    change_order_type_text = get_node_text(db, "change_order_type_message", CHANGE_ORDER_TYPE_MESSAGE)
    if change_order_type_text in last_bot_text:
        await _send_main_welcome_menu(db, wa_service, conv, contact, phone)
    elif conv.delivery_type in ("visit", "delivery", "pickup"):
        await _send_branch_selection_menu(db, wa_service, conv, contact, phone, prompt_body=get_node_text(db, "unknown_branch_message", UNKNOWN_BRANCH_MESSAGE))
    else:
        await _send_unknown_main_prompt(db, wa_service, conv, contact, phone)

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

        # Responder desde el mismo número de WhatsApp que recibió este mensaje. Esto evita
        # errores "Re-engagement message" cuando se cambia el número enrutado pero Railway
        # todavía conserva otro Phone Number ID como valor predeterminado.
        receiving_phone_id = msg_data.get("recipient_phone_number_id") or conv.whatsapp_phone_number_id
        wa_service = get_whatsapp_service(receiving_phone_id)
        message_type = msg_data.get("message_type", "text")
        text = msg_data.get("text", "")

        # Marca el mensaje como leído y muestra "escribiendo..." en el chat del cliente MIENTRAS
        # dura la pausa humana de abajo, para que la espera se sienta como alguien leyendo y
        # redactando la respuesta, no como un silencio. Efecto puramente cosmético: nunca lanza.
        incoming_wamid = msg_data.get("wamid")
        if incoming_wamid:
            await wa_service.send_typing_indicator(incoming_wamid)

        # Pausa antes de que el bot "empiece a escribir": evita que la respuesta llegue de forma
        # instantánea y poco natural. Configurable (0 en tests) vía settings.BOT_RESPONSE_DELAY_SECONDS.
        if settings.BOT_RESPONSE_DELAY_SECONDS > 0:
            # Una frase larga tarda ligeramente más que un "hola", con un tope bajo para no
            # perjudicar la experiencia. En tests el delay base es 0 y no añade espera.
            reading_time = min(len(text or "") * 0.015, 1.2)
            await asyncio.sleep(settings.BOT_RESPONSE_DELAY_SECONDS + reading_time)

        # Durante esos segundos de pausa el estado pudo cambiar: un agente pudo pausar el bot,
        # o pudo llegar otro mensaje del mismo cliente y avanzar el flujo. Se relee la
        # conversación para no contestar con datos obsoletos (y pisarle la conversación al
        # agente que ya la tomó).
        db.expire(conv)
        conv = db.query(Conversation).filter(
            Conversation.id == conv_id, Conversation.deleted_at.is_(None)
        ).first()
        if not conv:
            return

        # 0. Pedido estructurado enviado desde la Web App de Menú (/menu). Ya trae sucursal,
        #    entrega y pago resueltos (ver POST /api/orders/public), así que respondemos con un mensaje
        #    cálido y empático según el tipo de entrega (Delivery o Retiro) y pausamos el bot para que
        #    el agente de la sucursal tome el control personal de la conversación.
        if message_type == "text" and "MI PEDIDO FARMHOUSE" in text.upper():
            await _step_confirm_web_menu_order(db, wa_service, conv, contact, phone, text)
            return

        # 0.1 Atención humana solicitada explícitamente por el cliente. Si el bot ya está
        #     pausado, un agente ya está atendiendo: repetir el handoff solo volvería a
        #     anunciar lo mismo y dejaría otra nota interna de resumen duplicada.
        if message_type == "text" and not conv.automation_paused:
            if match_entry_intent(text) == "human":
                await _handoff_to_human(db, wa_service, conv, contact, phone)
                return

        # 1. Descargar archivo multimedia si existe y aún no fue descargado inline
        await _step_download_pending_media(db, wa_service, conv, message_type, msg_data, incoming_msg)

        # 2. Si la conversación tiene automatización pausada por un agente, no responder
        if conv.automation_paused:
            logger.info(f"[AutoResponse] Automatización pausada para conv {conv.id}. Omitiendo bot.")
            return

        # 3. Botones e intenciones universales. Funcionan aunque el cliente se salga del
        # camino lineal: volver, cancelar, cambiar sucursal/tipo o pedir una persona.
        interactive_id = str(msg_data.get("interactive_id") or "")

        if interactive_id == "main_human":
            await _handoff_to_human(db, wa_service, conv, contact, phone)
            return

        navigation_intent = match_navigation_intent(text) if message_type == "text" else None
        if interactive_id == "change_branch":
            navigation_intent = "change_branch"
        elif interactive_id == "nav_restart":
            navigation_intent = "restart"

        if navigation_intent in ("restart", "cancel"):
            await _step_handle_restart_or_cancel(db, wa_service, conv, contact, phone, navigation_intent)
            return

        if navigation_intent == "change_branch":
            await _step_handle_change_branch(db, wa_service, conv, contact, phone)
            return

        if navigation_intent in ("change_order_type", "back"):
            await _step_handle_change_order_type_or_back(db, wa_service, conv, contact, phone, navigation_intent, text, message_type)
            return

        # 2.55 "Pedir y pagar por chat": alternativa al Menú Digital web para quien ya sabe
        # exactamente qué quiere. Solo pide describir el pedido (texto libre, inevitable aquí)
        # y luego resuelve el pago con una lista tocable en vez de depender de que escriba la
        # palabra ("tarjeta"/"yappy"/"ach") de la nada.
        if interactive_id == "chat_order_start":
            await _step_start_chat_order(db, wa_service, conv, contact, phone)
            return

        if conv.awaiting_chat_order_description and message_type == "text" and text.strip():
            await _step_handle_chat_order_description(db, wa_service, conv, contact, phone)
            return

        # 2.6 Si hay una pregunta guiada de Corporativo/Evento pendiente, esta respuesta es
        # justo eso (no pasa por el resto del árbol de decisión hasta que se completen las 4).
        if conv.corporate_intake_step:
            await _handle_corporate_intake_step(db, wa_service, conv, contact, phone, interactive_id, message_type, text)
            return

        # 3.0 Detección de respuesta a "¿Te podemos ayudar en algo más?"
        manager_choice = None
        if interactive_id == "manager_yes":
            manager_choice = "yes"
        elif interactive_id == "manager_no":
            manager_choice = "no"
        elif interactive_id == "view_menu":
            manager_choice = "menu"
        elif conv.delivery_type == "visit" and message_type == "text":
            manager_choice = match_manager_help(text)

        if manager_choice == "yes":
            await _step_handle_manager_help_yes(db, wa_service, conv, contact, phone)
            return
        elif manager_choice == "no":
            await _step_handle_manager_help_no(db, wa_service, conv, contact, phone)
            return
        elif manager_choice == "menu":
            await _step_handle_manager_help_menu(db, wa_service, conv, contact, phone)
            return

        entry_intent = match_entry_intent(text) if message_type == "text" else None

        # 2.9 Cliente que ya sabe qué quiere y pide el menú directo: nos saltamos la pregunta
        # de Delivery/Retiro/Evento y solo pedimos la sucursal para armar el link del menú.
        if interactive_id == "main_menu_direct" or entry_intent == "menu_direct":
            await _send_branch_selection_menu(
                db, wa_service, conv, contact, phone, prompt_key="menu_direct"
            )
            return

        main_option_matched = None
        if interactive_id == "main_visit":
            main_option_matched = "visit"
        elif interactive_id == "order_delivery":
            main_option_matched = "delivery"
        elif interactive_id == "order_pickup":
            main_option_matched = "pickup"
        elif interactive_id == "order_corporate":
            main_option_matched = "corporate"
        elif message_type == "text":
            main_option_matched = match_main_option(text)

        # "Hacer un pedido" (escrito, o el botón main_order de un mensaje viejo ya en el chat
        # del cliente de antes de este cambio) ya no abre un submenú aparte — el menú principal
        # de bienvenida ya salta directo a Delivery/Retiro/Evento, así que reofrecerlo alcanza.
        if (interactive_id == "main_order" or entry_intent == "order") and not main_option_matched:
            await _send_main_welcome_menu(db, wa_service, conv, contact, phone)
            return

        # 3.1 Opción 4: Pedido Corporativo / Evento. Por defecto el bot no pregunta nada: entrega
        # de una el número del equipo de catering (ver CORPORATE_INTAKE_ENABLED en
        # auto_responses.py). Con el interruptor en True vuelve a hacer las 4 preguntas guiadas
        # antes de pasarle la conversación a Sol.
        if main_option_matched == "corporate":
            await _step_handle_corporate_option_selected(db, wa_service, conv, contact, phone)
            return

        # 3.2 Actualizar delivery_type si se seleccionó opción 1, 2 o 3
        if main_option_matched in ["visit", "delivery", "pickup"]:
            conv.delivery_type = main_option_matched
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

        # 4. Detección de sucursal si el cliente seleccionó una sucursal
        branch_matched_now = await _step_detect_and_assign_branch(db, conv, interactive_id, message_type, text)

        # 5. Si se seleccionó o tiene delivery_type (visit, delivery, pickup) pero falta sucursal:
        if conv.delivery_type in ["visit", "delivery", "pickup"] and conv.branch_id is None:
            await _send_branch_selection_menu(db, wa_service, conv, contact, phone, prompt_key=conv.delivery_type)
            return

        # 6. Si la sucursal fue elegida en este turno o acaba de completar delivery_type + sucursal:
        if (branch_matched_now or (main_option_matched in ["visit", "delivery", "pickup"] and conv.branch_id is not None)):
            await _send_branch_welcome_and_menu(db, wa_service, conv, contact, phone)
            return

        # 7. Si aún no hay contexto, mostrar entrada o recuperación contextual. Nunca se
        # omite una respuesta solo por haber mostrado el menú hace menos de tres minutos.
        if conv.delivery_type is None and conv.branch_id is None:
            await _step_prompt_entry_when_context_missing(db, wa_service, conv, contact, phone, message_type, text)
            return

        # 8. Flujo de selección de pago (si aplica dentro del chat)
        if conv.branch_id is not None and conv.delivery_type in ["delivery", "pickup"] and conv.payment_method is None:
            if await _step_handle_payment_selection(db, wa_service, conv, contact, phone, interactive_id, message_type, text):
                return

        # 8.1 Archivos/comprobantes: acusar recibo y entregarlos a una persona. Antes este
        # camino podía terminar sin ninguna respuesta visible para el cliente.
        if message_type not in ("text", "interactive"):
            await _handoff_to_human(
                db, wa_service, conv, contact, phone,
                get_node_text(db, "attachment_received_message", "Recibí tu archivo, gracias 📎 Ya se lo compartí al equipo para que lo revise y continúe contigo por aquí."),
            )
            return

        # 9. Recuperación contextual. Todo mensaje obtiene una salida útil: repetir la
        # decisión pertinente, ofrecer acciones o volver al inicio, nunca quedarse en silencio.
        await _step_recover_conversation_context(db, wa_service, conv, contact, phone, text)
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
    receiving_phone_id = str(msg_data.get("recipient_phone_number_id") or "").strip() or None

    # Ignorar mensajes de números que pertenecen a OTRO panel (ej. farmhouse-catering-center),
    # aunque compartan esta misma cuenta de WhatsApp Business. A diferencia de un futuro número
    # propio por sucursal (que sí debe procesarse aquí), estos están explícitamente excluidos.
    if receiving_phone_id and receiving_phone_id in settings.get_excluded_phone_number_ids():
        logger.info(
            f"[Webhook] Mensaje ignorado: phone_number_id {receiving_phone_id} pertenece a otro "
            f"panel (excluido explícitamente vía EXCLUDED_PHONE_NUMBER_IDS)."
        )
        return {"status": "ignored_excluded_phone_number_id"}

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
                whatsapp_phone_number_id=receiving_phone_id,
                created_at=now,
                updated_at=now
            )
            db.add(conv)
            db.flush()
            is_new_conv = True

        # Una conversación puede continuar después de cambiar la línea conectada. El webhook
        # de Meta es la fuente autoritativa para saber cuál número recibió el último mensaje.
        if receiving_phone_id and conv.whatsapp_phone_number_id != receiving_phone_id:
            conv.whatsapp_phone_number_id = receiving_phone_id

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

        # 8. Insertar mensaje entrante de forma atómica.
        #    media_type solo debe reflejar adjuntos reales: "interactive" (el cliente tocó un
        #    botón o eligió de una lista) no es un archivo, y guardarlo aquí hacía que el panel
        #    mostrara un falso "Descargando archivo de WhatsApp..." bajo cada respuesta de botón.
        is_real_media = message_type in ("image", "video", "audio", "document", "sticker")
        message = Message(
            conversation_id=conv.id,
            direction="incoming",
            sender_type="customer",
            content=text,
            whatsapp_message_id=wamid,
            is_internal=False,
            status="delivered",
            media_type=message_type if is_real_media else None,
            media_id=msg_data.get("media_id") if is_real_media else None,
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
