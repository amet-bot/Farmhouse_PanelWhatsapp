import json
import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.order import Order
from models.conversation import Conversation
from models.contact import Contact
from models.branch import Branch
from models.user import User
from schemas.order import OrderResponse, OrderCreate, OrderUpdate, PublicOrderCreate, PublicOrderResponse
from security.auth import get_current_authorized_user
from security.access_control import check_order_access, check_conversation_access, check_target_branch_valid
from services.menu_catalog import get_item_by_sku, clean_item_title
from services.websocket_manager import ws_manager

logger = logging.getLogger("farmhouse.orders")

router = APIRouter(prefix="/orders", tags=["Pedidos"])

ITBMS_RATE = Decimal("0.07") # 7% impuesto ITBMS en Panamá
DELIVERY_SURCHARGE = Decimal("3.50")

PAYMENT_METHOD_LABELS = {"yappy": "Yappy", "ach": "ACH / Transferencia", "card": "Tarjeta", "cash": "Efectivo"}

@router.post("/public", response_model=PublicOrderResponse)
async def create_public_order(
    order_in: PublicOrderCreate,
    db: Session = Depends(get_db)
):
    """
    Endpoint público (sin autenticación) usado por la Web App de Menú Digital (/menu).
    Recalcula todos los precios desde el catálogo del servidor (nunca confía en el precio
    que manda el navegador), crea/actualiza el contacto y la conversación del cliente,
    registra la comanda y devuelve el enlace wa.me para que el cliente confirme el pedido
    por WhatsApp.
    """
    branch = db.query(Branch).filter(Branch.code == order_in.branch_code, Branch.active == True).first()
    if not branch:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sucursal inválida o inactiva.")

    if order_in.delivery_type == "delivery" and not (order_in.delivery_address or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La dirección de entrega es obligatoria para pedidos de delivery.")

    # 1. Recalcular cada línea del pedido desde el catálogo (fuente de verdad de precios)
    line_items = []
    subtotal = Decimal("0.00")
    for raw_item in order_in.items:
        catalog_item = get_item_by_sku(raw_item.sku)
        if not catalog_item:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Producto no encontrado en el catálogo: {raw_item.sku}")

        addons = []
        addons_total = Decimal("0.00")
        for addon_sku in raw_item.addon_skus:
            addon_item = get_item_by_sku(addon_sku)
            if not addon_item:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Adicional no encontrado en el catálogo: {addon_sku}")
            addons.append({"sku": addon_item["sku"], "title": clean_item_title(addon_item["title"]), "price": float(addon_item["price"])})
            addons_total += addon_item["price"]

        unit_price = catalog_item["price"] + addons_total
        line_total = (unit_price * raw_item.quantity).quantize(Decimal("0.01"))
        subtotal += line_total

        line_items.append({
            "sku": catalog_item["sku"],
            "title": catalog_item["title"],
            "quantity": raw_item.quantity,
            "unit_price": float(catalog_item["price"]),
            "addons": addons,
            "notes": raw_item.notes,
            "line_total": float(line_total),
        })

    subtotal = subtotal.quantize(Decimal("0.01"))
    delivery_cost = DELIVERY_SURCHARGE if order_in.delivery_type == "delivery" else Decimal("0.00")
    total = (subtotal + delivery_cost).quantize(Decimal("0.01"))

    # 2. Contacto: mismo formato "+<digitos>" que usa el webhook de Meta, para que ambos
    #    flujos (esta orden y el mensaje real de WhatsApp) apunten al mismo registro.
    phone_digits = "".join(c for c in order_in.customer_phone if c.isdigit())
    if not phone_digits:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Teléfono de cliente inválido.")
    phone = f"+{phone_digits}"

    now = datetime.now(timezone.utc)
    contact = db.query(Contact).filter(Contact.phone == phone).first()
    if not contact:
        contact = Contact(name=order_in.customer_name.strip(), phone=phone, created_at=now, last_interaction=now)
        db.add(contact)
        db.flush()
    else:
        contact.last_interaction = now
        if contact.deleted_at:
            contact.deleted_at = None
        if order_in.customer_name.strip():
            contact.name = order_in.customer_name.strip()

    # 3. Conversación activa (o nueva), con la sucursal/entrega/pago ya resueltos para
    #    que el bot de WhatsApp no vuelva a preguntarlos cuando llegue el mensaje real.
    conv = db.query(Conversation).filter(
        Conversation.customer_id == contact.id,
        Conversation.status.in_(["new", "unassigned", "open", "pending"]),
        Conversation.deleted_at.is_(None)
    ).order_by(Conversation.updated_at.desc()).first()

    is_new_conv = False
    if not conv:
        conv = Conversation(customer_id=contact.id, status="unassigned", created_at=now, updated_at=now)
        db.add(conv)
        db.flush()
        is_new_conv = True

    conv.branch_id = branch.id
    conv.delivery_type = order_in.delivery_type
    conv.payment_method = order_in.payment_method
    conv.updated_at = now

    # 4. Registrar la comanda
    order_code = f"FH-{uuid.uuid4().hex[:6].upper()}"
    order_type = "takeout" if order_in.delivery_type == "pickup" else "delivery"
    order = Order(
        order_code=order_code,
        conversation_id=conv.id,
        branch_id=branch.id,
        order_type=order_type,
        status="en_proceso",
        subtotal=subtotal,
        delivery_cost=delivery_cost,
        tax=Decimal("0.00"),
        total=total,
        items_json=json.dumps({
            "items": line_items,
            "delivery_address": order_in.delivery_address,
            "payment_method": order_in.payment_method,
            "source": "menu_web",
        }, ensure_ascii=False),
        created_by=None,
        created_at=now
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    db.refresh(conv)

    # 5. Construir el mensaje estructurado que el cliente confirmará en WhatsApp
    whatsapp_text = _build_whatsapp_order_text(order_code, branch.name, line_items, order_in, delivery_cost, total)
    display_number = "".join(c for c in str(settings.META_WA_DISPLAY_NUMBER or "") if c.isdigit())
    whatsapp_url = f"https://wa.me/{display_number}?text={quote(whatsapp_text)}"

    logger.info(f"[PublicOrder] Comanda {order_code} creada desde /menu para conv {conv.id} (Total: ${total})")

    await ws_manager.broadcast_to_branch(branch.id, {
        "type": "order_created",
        "conversation_id": conv.id,
        "branch_id": branch.id,
        "contact_name": contact.name,
        "contact_phone": contact.phone,
        "is_new_conversation": is_new_conv,
        "order": {
            "id": order.id,
            "order_code": order.order_code,
            "subtotal": float(order.subtotal),
            "delivery_cost": float(order.delivery_cost),
            "total": float(order.total),
            "order_type": order.order_type,
            "status": order.status,
        }
    })

    return PublicOrderResponse(
        order_code=order_code,
        conversation_id=conv.id,
        subtotal=subtotal,
        delivery_cost=delivery_cost,
        total=total,
        whatsapp_url=whatsapp_url,
    )


def _build_whatsapp_order_text(order_code: str, branch_name: str, line_items: list, order_in: PublicOrderCreate, delivery_cost: Decimal, total: Decimal) -> str:
    # NOTA: la página de "click to chat" de WhatsApp (wa.me / api.whatsapp.com/send) corrompe
    # los emojis de 4 bytes (fuera del BMP, ej. 🥗📍🛵💳) en el parámetro `text`, mostrando "�"
    # en su lugar (confirmado probando enlaces wa.me mínimos). Los acentos en español y símbolos
    # de 1-3 bytes (•, *negritas* de WhatsApp) sí se transmiten bien, así que el mensaje usa solo eso.
    # El aviso de confirmación que manda el bot SÍ puede usar emoji normalmente porque se envía por
    # la Graph API de Meta (JSON), no por este enlace.
    lines = ["*MI PEDIDO FARMHOUSE*", f"Sucursal: {branch_name}", "Ítems:"]
    for item in line_items:
        lines.append(f"• {item['quantity']}x {item['title']} - ${item['line_total']:.2f}")
        for addon in item["addons"]:
            lines.append(f"   + {addon['title']} (${addon['price']:.2f})")
        if item.get("notes"):
            lines.append(f"   Nota: {item['notes']}")

    entrega_label = "Delivery" if order_in.delivery_type == "delivery" else "Retiro en Sucursal"
    lines.append(f"Entrega: {entrega_label}" + (f" (+${delivery_cost:.2f})" if delivery_cost else ""))
    if order_in.delivery_type == "delivery" and order_in.delivery_address:
        lines.append(f"Dirección: {order_in.delivery_address}")
    lines.append(f"Método de pago: {PAYMENT_METHOD_LABELS[order_in.payment_method]}")
    lines.append(f"TOTAL: ${total:.2f}")
    lines.append(f"Pedido: {order_code}")
    return "\n".join(lines)


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

