"""
Embudo del bot: cuántas conversaciones entran y hasta dónde llegan (opción elegida, sucursal,
enlace del menú, pedido, pago, persona). Se calcula al vuelo sobre las tablas que ya existen —
no hay tabla de eventos — así que son conteos aproximados pero suficientes para ver en qué
paso se pierden los clientes. Se muestra en el encabezado de Flujo Visual.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db
from models.conversation import Conversation
from models.message import Message
from models.order import Order
from security.auth import require_role

router = APIRouter(prefix="/bot", tags=["Bot"])

MENU_LINK_MARKER = "[Botón: Ver menú"
HANDOFF_MARKER = "📋 Contexto recopilado por el asistente"


@router.get("/funnel", dependencies=[Depends(require_role(["admin", "supervisor"]))])
def bot_funnel(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    base = db.query(Conversation).filter(Conversation.deleted_at.is_(None), Conversation.created_at >= since)

    started = base.count()
    # "Eligió algo": tipo de entrega o sucursal (quien va directo al menú elige sucursal sin
    # pasar por delivery/retiro), para que ningún paso posterior supere al anterior.
    chose_option = base.filter(or_(Conversation.delivery_type.isnot(None), Conversation.branch_id.isnot(None))).count()
    with_branch = base.filter(Conversation.branch_id.isnot(None)).count()

    conv_ids = [c.id for c in base.with_entities(Conversation.id).all()]
    menu_sent = handoffs = 0
    if conv_ids:
        menu_sent = db.query(func.count(func.distinct(Message.conversation_id))).filter(
            Message.conversation_id.in_(conv_ids), Message.deleted_at.is_(None),
            Message.direction == "outgoing", Message.content.like(f"%{MENU_LINK_MARKER}%"),
        ).scalar() or 0
        handoffs = db.query(func.count(func.distinct(Message.conversation_id))).filter(
            Message.conversation_id.in_(conv_ids), Message.is_internal == True,  # noqa: E712
            Message.content.like(f"{HANDOFF_MARKER}%"),
        ).scalar() or 0

    orders_q = db.query(Order).filter(
        Order.deleted_at.is_(None), Order.created_at >= since,
        Order.status.notin_(["carrito_activo", "abandonado"]),
    )
    orders = orders_q.count()
    paid = orders_q.filter(Order.payment_status == "paid").count()
    escalated = base.filter(Conversation.handoff_escalated_at.isnot(None)).count()

    return {
        "days": days,
        "started": started,
        "chose_option": chose_option,
        "with_branch": with_branch,
        "menu_sent": menu_sent,
        "orders": orders,
        "paid": paid,
        "handoffs": handoffs,
        "handoffs_escalated": escalated,
    }
