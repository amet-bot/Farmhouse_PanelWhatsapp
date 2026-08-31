import json
import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from database import get_db
from models.order import Order
from models.conversation import Conversation
from models.user import User
from schemas.order import OrderResponse, OrderCreate, OrderUpdate
from security.auth import get_current_authorized_user
from security.access_control import check_order_access, check_conversation_access, check_target_branch_valid

logger = logging.getLogger("farmhouse.orders")

router = APIRouter(prefix="/orders", tags=["Pedidos"])

ITBMS_RATE = Decimal("0.07") # 7% impuesto ITBMS en Panamá

@router.post("/", response_model=OrderResponse)
def create_order(
    order_in: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Crea una comanda/pedido vinculada a una conversación con cálculo exacto de decimales (Punto 8).
    """
    # 1. Validar acceso a la conversación
    conv = check_conversation_access(db, order_in.conversation_id, current_user, action="create_order")
    
    # 2. Validar sucursal para agentes
    if current_user.role == "agent" and order_in.branch_id != current_user.branch_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para crear pedidos en otra sucursal."
        )

    branch = check_target_branch_valid(db, order_in.branch_id)

    # 3. Cálculos monetarios en Decimal
    subtotal = Decimal(str(order_in.subtotal)).quantize(Decimal("0.01"))
    delivery_cost = Decimal(str(order_in.delivery_cost)).quantize(Decimal("0.01"))
    tax = (subtotal * ITBMS_RATE).quantize(Decimal("0.01"))
    total = (subtotal + tax + delivery_cost).quantize(Decimal("0.01"))

    # Generar código único de comanda (ej: FH-004812)
    order_code = f"FH-{uuid.uuid4().hex[:6].upper()}"

    # Serializar items si se enviaron
    items_json = order_in.items_json
    if order_in.items:
        items_json = json.dumps([item.model_dump(mode="json") for item in order_in.items])

    now = datetime.now(timezone.utc)
    order = Order(
        order_code=order_code,
        conversation_id=order_in.conversation_id,
        branch_id=order_in.branch_id,
        order_type=order_in.order_type.value,
        status="en_proceso",
        subtotal=subtotal,
        delivery_cost=delivery_cost,
        tax=tax,
        total=total,
        items_json=items_json,
        created_by=current_user.id,
        created_at=now
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    logger.info(f"Comanda {order_code} creada para conv {conv.id} por {current_user.name} (Total: ${total})")
    return order

@router.get("/conversation/{conversation_id}", response_model=List[OrderResponse])
def get_orders_by_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    check_conversation_access(db, conversation_id, current_user, action="read_orders")
    orders = db.query(Order).filter(
        Order.conversation_id == conversation_id,
        Order.deleted_at.is_(None)
    ).all()
    return orders

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    return check_order_access(db, order_id, current_user, action="read")

@router.put("/{order_id}", response_model=OrderResponse)
def update_order(
    order_id: int,
    order_in: OrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    order = check_order_access(db, order_id, current_user, action="update")

    update_data = order_in.model_dump(exclude_unset=True)
    
    if "status" in update_data and update_data["status"]:
        order.status = update_data["status"].value
    if "order_type" in update_data and update_data["order_type"]:
        order.order_type = update_data["order_type"].value
    if "items_json" in update_data:
        order.items_json = update_data["items_json"]

    # Recalcular si cambiaron importes
    recalc = False
    if "subtotal" in update_data and update_data["subtotal"] is not None:
        order.subtotal = Decimal(str(update_data["subtotal"])).quantize(Decimal("0.01"))
        recalc = True
    if "delivery_cost" in update_data and update_data["delivery_cost"] is not None:
        order.delivery_cost = Decimal(str(update_data["delivery_cost"])).quantize(Decimal("0.01"))
        recalc = True

    if recalc:
        order.tax = (order.subtotal * ITBMS_RATE).quantize(Decimal("0.01"))
        order.total = (order.subtotal + order.tax + order.delivery_cost).quantize(Decimal("0.01"))

    db.commit()
    db.refresh(order)
    logger.info(f"Comanda ID {order_id} ({order.order_code}) actualizada por usuario ID {current_user.id}")
    return order

@router.delete("/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    order = check_order_access(db, order_id, current_user, action="delete")
    order.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "deleted", "order_id": order_id}

