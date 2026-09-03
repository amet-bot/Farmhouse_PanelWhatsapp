"""
Carrito activo del Menú Digital (/menu), sincronizado en tiempo real con el panel.

Reutiliza la tabla `orders` existente en vez de crear una entidad nueva (Punto 23 del
pedido del usuario): un carrito activo es una fila de `orders` con status="carrito_activo".
Existe COMO MÁXIMO una fila con ese status por conversación — nunca se crea un pedido nuevo
por cada click del cliente en el menú (Punto 12), siempre se actualiza la misma fila hasta
que el cliente confirma el pedido (entonces pasa a status="en_proceso" con un order_code real)
o abandona el carrito (entonces pasa a status="abandonado" tras CART_EXPIRY_MINUTES de
inactividad, ver expire_if_stale).
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from models.order import Order

logger = logging.getLogger("farmhouse.active_cart")

CART_STATUS = "carrito_activo"
ABANDONED_STATUS = "abandonado"

# No existía ninguna política previa de expiración de sesiones/carritos en el proyecto que
# reutilizar (auditado), así que se introduce este valor conservador y explícito (Punto 16).
CART_EXPIRY_MINUTES = 30


def _now():
    # Naive UTC a propósito: las columnas DATETIME de MySQL (y SQLite en los tests) no
    # conservan tzinfo, así que un datetime "aware" comparado contra un valor recién leído
    # de la base (naive) revienta con TypeError. El resto del proyecto ya asume implícitamente
    # que todo DATETIME es UTC sin marca de zona; se mantiene esa misma convención aquí.
    return datetime.utcnow()


def get_active_cart(db: Session, conversation_id: int) -> Optional[Order]:
    """
    Carrito activo vigente de una conversación, o None si no hay ninguno o si el que había
    quedó abandonado (en cuyo caso se marca "abandonado" de forma perezosa, sin necesitar un
    scheduler/cron adicional que el proyecto no tiene hoy).
    """
    cart = db.query(Order).filter(
        Order.conversation_id == conversation_id,
        Order.status == CART_STATUS,
        Order.deleted_at.is_(None),
    ).order_by(Order.id.desc()).first()

    if not cart:
        return None

    if cart.expires_at and cart.expires_at < _now():
        cart.status = ABANDONED_STATUS
        db.commit()
        logger.info(f"[ActiveCart] Carrito de conv {conversation_id} marcado abandonado por inactividad (order id {cart.id}).")
        return None

    return cart


def expire_stale_carts_for_conversations(db: Session, conversation_ids: list) -> None:
    """Versión en lote de get_active_cart, para listados (evita N+1 al expirar carritos viejos)."""
    if not conversation_ids:
        return
    now = _now()
    stale = db.query(Order).filter(
        Order.conversation_id.in_(conversation_ids),
        Order.status == CART_STATUS,
        Order.deleted_at.is_(None),
        Order.expires_at.isnot(None),
        Order.expires_at < now,
    ).all()
    if not stale:
        return
    for cart in stale:
        cart.status = ABANDONED_STATUS
    db.commit()


def upsert_active_cart(
    db: Session,
    conversation_id: int,
    branch_id: int,
    line_items: list,
    subtotal: Decimal,
    delivery_fee: Decimal,
    total: Decimal,
    delivery_type: str,
    delivery_address: Optional[str],
    payment_method: Optional[str],
) -> Order:
    """Crea o actualiza LA (única) fila de carrito activo de esta conversación."""
    cart = db.query(Order).filter(
        Order.conversation_id == conversation_id,
        Order.status == CART_STATUS,
        Order.deleted_at.is_(None),
    ).order_by(Order.id.desc()).first()

    now = _now()
    items_payload = json.dumps({
        "items": line_items,
        "delivery_address": delivery_address,
        "payment_method": payment_method,
        "source": "menu_web_cart",
    }, ensure_ascii=False)

    if cart:
        cart.branch_id = branch_id
        cart.order_type = "takeout" if delivery_type == "pickup" else "delivery"
        cart.subtotal = subtotal
        cart.delivery_cost = delivery_fee
        cart.total = total
        cart.items_json = items_payload
        cart.updated_at = now
        cart.expires_at = now + timedelta(minutes=CART_EXPIRY_MINUTES)
    else:
        cart = Order(
            order_code=f"CART-{uuid.uuid4().hex[:8].upper()}",
            conversation_id=conversation_id,
            branch_id=branch_id,
            order_type="takeout" if delivery_type == "pickup" else "delivery",
            status=CART_STATUS,
            subtotal=subtotal,
            delivery_cost=delivery_fee,
            tax=Decimal("0.00"),
            total=total,
            items_json=items_payload,
            created_by=None,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=CART_EXPIRY_MINUTES),
        )
        db.add(cart)

    db.commit()
    db.refresh(cart)
    return cart


def clear_active_cart(db: Session, conversation_id: int) -> None:
    """Cliente vació el carrito (quitó el último producto): se elimina la fila de borrador."""
    cart = db.query(Order).filter(
        Order.conversation_id == conversation_id,
        Order.status == CART_STATUS,
        Order.deleted_at.is_(None),
    ).first()
    if cart:
        db.delete(cart)
        db.commit()


def cart_to_dict(cart: Optional[Order]) -> dict:
    """Forma serializable para WebSocket y para la respuesta HTTP del endpoint de carrito."""
    if not cart:
        return {"status": "empty", "items": [], "subtotal": "0.00", "delivery_fee": "0.00", "total": "0.00"}

    items_data = {}
    try:
        items_data = json.loads(cart.items_json) if cart.items_json else {}
    except (json.JSONDecodeError, TypeError):
        items_data = {}

    return {
        "order_id": cart.id,
        "order_code": cart.order_code,
        "conversation_id": cart.conversation_id,
        "branch_id": cart.branch_id,
        "status": cart.status,
        "order_type": cart.order_type,
        "items": items_data.get("items", []),
        "delivery_address": items_data.get("delivery_address"),
        "payment_method": items_data.get("payment_method"),
        "subtotal": str(cart.subtotal),
        "delivery_fee": str(cart.delivery_cost),
        "total": str(cart.total),
        "updated_at": cart.updated_at.isoformat() if cart.updated_at else None,
    }
