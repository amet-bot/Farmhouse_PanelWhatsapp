import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.message import Message
from models.order import Order
from models.user import User
from security.access_control import check_conversation_access
from security.auth import get_current_authorized_user
from services.auto_responses import get_yappy_payment_success_message
from services.websocket_manager import ws_manager
from services.whatsapp_service import get_whatsapp_service
from services.yappy_payment import (
    YappyConfigurationError,
    YappyGatewayError,
    build_yappy_payment_url,
    create_yappy_order,
    decode_yappy_payment_token,
    is_yappy_configured,
    local_yappy_alias_or_none,
    verify_yappy_ipn,
    yappy_domain,
)

logger = logging.getLogger("farmhouse.payments")
router = APIRouter(prefix="/payments/yappy", tags=["Pagos Yappy"])


class YappySessionRequest(BaseModel):
    order_code: str
    token: str
    phone: Optional[str] = None


class YappyChargeRequest(BaseModel):
    """Cobro armado desde el panel para un pedido por chat: monto final + qué pidió."""
    conversation_id: int
    total: Decimal = Field(..., gt=0, le=Decimal("5000"))
    description: Optional[str] = Field(None, max_length=500)


def _payment_method(order: Order) -> Optional[str]:
    try:
        return json.loads(order.items_json or "{}").get("payment_method")
    except (TypeError, ValueError):
        return None


def _authorized_order(db: Session, order_code: str, token: str) -> Order:
    if not decode_yappy_payment_token(token, order_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El enlace de pago no es válido o venció.",
        )
    order = (
        db.query(Order)
        .filter(Order.order_code == order_code, Order.deleted_at.is_(None))
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado."
        )
    if _payment_method(order) != "yappy":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este pedido no seleccionó Yappy como método de pago.",
        )
    return order


@router.post("/charge")
async def create_yappy_charge(
    payload: YappyChargeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    "Cobrar con Yappy" desde el panel. Para los pedidos que el cliente describe por chat
    ("Pedir y pagar por chat") no existía ningún pedido ni forma de mandarle el botón: el bot
    prometía "un botón con el monto exacto" que solo salía para pedidos del menú web. Aquí la
    persona del equipo pone el total (ya con delivery si aplica) y se crea el pedido y se manda
    el mismo botón de Yappy que usa el menú web. El pago se confirma solo por el IPN real.
    """
    if not is_yappy_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Yappy todavía no está activado para este comercio.")
    conv = check_conversation_access(db, payload.conversation_id, current_user, action="yappy_charge")
    if conv.branch_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asigna primero una sucursal a la conversación.")
    contact = conv.contact
    if not contact or not contact.phone:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La conversación no tiene un teléfono al que mandar el botón.")

    total = Decimal(payload.total).quantize(Decimal("0.01"))
    description = " ".join((payload.description or "").split())[:500] or (conv.chat_order_description or "")[:500]
    now = datetime.now(timezone.utc)
    order = Order(
        order_code=f"FH-{uuid.uuid4().hex[:6].upper()}",
        conversation_id=conv.id,
        branch_id=conv.branch_id,
        source="chat",
        order_type="takeout" if conv.delivery_type == "pickup" else "delivery",
        status="en_proceso",
        subtotal=total,
        delivery_cost=Decimal("0.00"),
        tax=Decimal("0.00"),
        total=total,
        payment_status="pending",
        items_json=json.dumps({"items": [], "description": description, "payment_method": "yappy", "source": "chat"}, ensure_ascii=False),
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    conv.payment_method = "yappy"
    conv.updated_at = now
    db.commit()
    db.refresh(order)

    payment_url = build_yappy_payment_url(order.order_code)
    text = (
        f"Tu pedido {order.order_code} por ${total:.2f} está listo para pagar con Yappy. "
        "Toca el botón para recibir y aprobar la solicitud en tu aplicación Yappy."
    )
    wamid = None
    message_status = "sent"
    error_detail = None
    try:
        result = await get_whatsapp_service(conv.whatsapp_phone_number_id).send_cta_url_message(
            contact.phone, text, "Pagar con Yappy", payment_url
        )
        if isinstance(result, dict) and result.get("messages"):
            wamid = result["messages"][0].get("id")
    except Exception as exc:
        message_status = "failed"
        error_detail = str(exc)[:500]
        logger.error("[YappyCharge] No se pudo mandar el botón del pedido %s: %s", order.order_code, exc)
    # Lo manda una persona del equipo (sender_type "agent"): cuenta como respuesta al cliente
    # para el escalamiento de handoffs sin atender (services/bot_followup.py).
    msg = Message(
        conversation_id=conv.id, direction="outgoing", sender_type="agent",
        content=f"{text}\n{payment_url}", whatsapp_message_id=wamid, is_internal=False,
        status=message_status, error_detail=error_detail,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "new_incoming_message", "conversation_id": conv.id, "branch_id": conv.branch_id,
        "contact_name": contact.name, "contact_phone": contact.phone,
        "message": {"id": msg.id, "direction": msg.direction, "sender_type": msg.sender_type,
                    "content": msg.content, "status": msg.status, "created_at": msg.created_at.isoformat()},
        "is_new_conversation": False,
    })
    await ws_manager.broadcast_to_branch(conv.branch_id, {
        "type": "order_created", "conversation_id": conv.id, "branch_id": conv.branch_id,
        "order_id": order.id, "order_code": order.order_code,
    })
    return {
        "order_id": order.id, "order_code": order.order_code, "total": f"{total:.2f}",
        "payment_url": payment_url, "message_status": message_status,
    }


@router.get("/config")
def get_yappy_public_config():
    return {
        "enabled": is_yappy_configured(),
        "button_cdn_url": settings.YAPPY_BUTTON_CDN_URL,
    }


@router.get("/orders/{order_code}")
def get_yappy_order(
    order_code: str, token: str = Query(...), db: Session = Depends(get_db)
):
    order = _authorized_order(db, order_code, token)
    return {
        "order_code": order.order_code,
        "total": f"{order.total:.2f}",
        "payment_status": order.payment_status,
        "configured": is_yappy_configured(),
        "contact_phone": local_yappy_alias_or_none(order.conversation.contact.phone),
    }


@router.post("/session")
async def start_yappy_session(
    payload: YappySessionRequest, db: Session = Depends(get_db)
):
    order = _authorized_order(db, payload.order_code, payload.token)
    if order.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Este pedido ya está pagado."
        )
    phone = (payload.phone or "").strip() or order.conversation.contact.phone
    try:
        result = await create_yappy_order(
            order_code=order.order_code,
            phone=phone,
            total=order.total,
        )
    except YappyConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except YappyGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    order.payment_status = "awaiting_customer"
    order.payment_reference = str(result["transactionId"])
    db.commit()
    return {
        "transactionId": result["transactionId"],
        "token": result["token"],
        "documentName": result["documentName"],
    }


@router.get("/ipn")
async def yappy_ipn(
    order_id: str = Query(..., alias="orderId"),
    payment_status: str = Query(..., alias="status"),
    received_hash_upper: Optional[str] = Query(None, alias="Hash"),
    received_hash_lower: Optional[str] = Query(None, alias="hash"),
    domain: str = Query(...),
    confirmation_number: Optional[str] = Query(None, alias="confirmationNumber"),
    db: Session = Depends(get_db),
):
    # Sin credenciales de Yappy configuradas, la clave de firma queda vacía y CUALQUIERA podría
    # calcular un hash válido (el dominio es público) y marcar un pedido como pagado. Este
    # endpoint es público, así que se rechaza de plano mientras Yappy no esté configurado.
    if not is_yappy_configured():
        logger.warning(
            "[Yappy IPN] Notificación recibida para la orden %s con Yappy sin configurar. Rechazada.",
            order_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Los pagos por Yappy no están habilitados.",
        )

    received_hash = received_hash_upper or received_hash_lower or ""
    if domain != yappy_domain() or not verify_yappy_ipn(
        order_id, payment_status, domain, received_hash
    ):
        logger.warning("[Yappy IPN] Firma inválida para la orden %s", order_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Firma de Yappy inválida."
        )
    order = (
        db.query(Order)
        .filter(Order.order_code == order_id, Order.deleted_at.is_(None))
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado."
        )

    was_already_paid = order.payment_status == "paid"

    status_map = {"E": "paid", "R": "rejected", "C": "cancelled", "X": "expired"}
    new_payment_status = status_map.get(payment_status)
    if new_payment_status is None:
        # Código desconocido (Yappy podría agregar nuevos): se registra sin tocar el estado,
        # en vez de dejar el pedido en "unknown" y perder el estado real que ya tenía.
        logger.warning(
            "[Yappy IPN] Estado '%s' no reconocido para la orden %s. Se conserva '%s'.",
            payment_status, order_id, order.payment_status,
        )
    elif order.payment_status == "paid" and new_payment_status != "paid":
        # Una notificación posterior (ej. la expiración de otro intento de la misma orden) no
        # debe degradar un pedido que ya fue pagado y confirmado.
        logger.warning(
            "[Yappy IPN] Se ignora '%s' para la orden %s porque ya está pagada.",
            payment_status, order_id,
        )
    else:
        order.payment_status = new_payment_status

    if confirmation_number:
        order.payment_confirmation_number = confirmation_number
    db.commit()

    # Al cliente solo se le avisa en el instante exacto en que el pago se confirma de verdad
    # (firma de Yappy ya verificada arriba, nunca por suposición) — no en cada IPN que llegue
    # mientras siga "paid", así Yappy puede reintentar la notificación sin que el cliente
    # reciba el mismo "pago exitoso" varias veces.
    just_got_paid = (not was_already_paid) and order.payment_status == "paid"
    if just_got_paid:
        await _notify_customer_payment_success(db, order)

    await ws_manager.broadcast_to_branch(
        order.branch_id,
        {
            "type": "order_payment_updated",
            "conversation_id": order.conversation_id,
            "branch_id": order.branch_id,
            "order_id": order.id,
            "order_code": order.order_code,
            "payment_status": order.payment_status,
        },
    )
    return {"success": True, "payment_status": order.payment_status}


async def _notify_customer_payment_success(db: Session, order: Order) -> None:
    """Le avisa al CLIENTE (no solo al panel) que su pedido ya está pagado y confirmado —
    hoy esta notificación no existía: el IPN solo actualizaba el estado interno."""
    conv = order.conversation
    contact = conv.contact if conv else None
    if not conv or not contact or not contact.phone:
        logger.warning(
            "[Yappy IPN] Orden %s se marcó pagada pero no tiene conversación/contacto válido para avisarle al cliente.",
            order.order_code,
        )
        return

    # Import diferido: evita un ciclo de imports, mismo motivo que en services/bot_followup.py
    # (routers.webhooks no necesita este módulo).
    from routers.webhooks import _send_plain_text_message

    wa_service = get_whatsapp_service(conv.whatsapp_phone_number_id)
    text = get_yappy_payment_success_message(order.order_code, db=db)
    await _send_plain_text_message(db, wa_service, conv, contact, contact.phone, text)
