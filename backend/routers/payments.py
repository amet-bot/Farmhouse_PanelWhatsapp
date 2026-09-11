import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.order import Order
from services.websocket_manager import ws_manager
from services.yappy_payment import (
    YappyConfigurationError,
    YappyGatewayError,
    create_yappy_order,
    decode_yappy_payment_token,
    is_yappy_configured,
    verify_yappy_ipn,
    yappy_domain,
)

logger = logging.getLogger("farmhouse.payments")
router = APIRouter(prefix="/payments/yappy", tags=["Pagos Yappy"])


class YappySessionRequest(BaseModel):
    order_code: str
    token: str


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
    try:
        result = await create_yappy_order(
            order_code=order.order_code,
            phone=order.conversation.contact.phone,
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
