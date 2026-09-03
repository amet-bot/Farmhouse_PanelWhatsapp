import json
import logging
import re
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from config import settings, get_official_whatsapp_number, get_whatsapp_number_for_branch, get_all_official_whatsapp_numbers
from database import get_db
from models.order import Order
from models.conversation import Conversation
from models.contact import Contact
from models.branch import Branch
from models.user import User
from schemas.order import (
    OrderResponse, OrderCreate, OrderUpdate, PublicOrderCreate, PublicOrderResponse,
    CartSyncRequest, CartSyncResponse,
)
from security.auth import get_current_authorized_user, decode_menu_session_token
from security.access_control import check_order_access, check_conversation_access, check_target_branch_valid
from services.order_pricing import price_cart_items, compute_delivery_fee
from services.active_cart import get_active_cart, upsert_active_cart, clear_active_cart, cart_to_dict
from services.websocket_manager import ws_manager

logger = logging.getLogger("farmhouse.orders")

router = APIRouter(prefix="/orders", tags=["Pedidos"])

ITBMS_RATE = Decimal("0.07") # 7% impuesto ITBMS en Panamá

PAYMENT_METHOD_LABELS = {"yappy": "Yappy", "ach": "ACH / Transferencia", "card": "Tarjeta", "cash": "Efectivo"}
WA_NUMBER_RE = re.compile(r"^\d{8,15}$")


def resolve_whatsapp_destination(branch_code: str, origin_wa: Optional[str]) -> str:
    """
    Decide a qué número de WhatsApp debe apuntar el enlace `wa.me` del pedido.

    Prioridad:
      1. Número propio de la sucursal seleccionada (get_whatsapp_number_for_branch) — hoy
         siempre resuelve al número oficial general, porque ninguna sucursal (Obarrio incluida)
         tiene todavía una línea de WhatsApp propia configurada en BRANCH_WHATSAPP_NUMBERS.
      2. `origin_wa` (el `?wa=` que trae /menu, ver webhooks._send_branch_welcome_and_menu) —
         SOLO si es un teléfono válido y coincide con alguno de los números oficiales conocidos
         del sistema. Nunca se confía en un valor arbitrario: esto es lo único que evita que
         alguien edite la URL del menú para redirigir pedidos a otro número.
      3. El paso 1 ya cubre el número general como último recurso, así que este resolver nunca
         devuelve una cadena vacía: si ni siquiera el número oficial general está configurado,
         propaga la excepción para que el endpoint falle con un 500 explícito en vez de generar
         un enlace `wa.me/?text=` sin destino (que hace que WhatsApp muestre su selector de chats).
    """
    branch_number = get_whatsapp_number_for_branch(branch_code)

    candidate = "".join(c for c in str(origin_wa or "") if c.isdigit())
    if candidate:
        if WA_NUMBER_RE.match(candidate) and candidate in get_all_official_whatsapp_numbers():
            return candidate
        logger.warning(
            f"[PublicOrder] Se recibió ?wa={candidate!r} pero no coincide con ningún número "
            f"oficial configurado; se ignora y se usa el número de la sucursal ({branch_number})."
        )

    return branch_number

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

    # Resolver el número de WhatsApp destino ANTES de tocar la base de datos: si el número
    # oficial no está configurado, es mejor fallar rápido con un 500 explícito que crear el
    # pedido y luego devolver un enlace wa.me sin destino (WhatsApp mostraría su selector de
    # chats en vez de abrir la conversación de Farmhouse).
    try:
        whatsapp_destination = resolve_whatsapp_destination(order_in.branch_code, order_in.origin_wa)
    except RuntimeError as e:
        logger.error(f"[PublicOrder] {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El envío de pedidos por WhatsApp no está disponible en este momento. Por favor contacta directamente a la sucursal.",
        )

    # 1. Recalcular cada línea del pedido desde el catálogo (fuente de verdad de precios,
    #    compartida con PUT /orders/cart y con el mensaje de WhatsApp — services.order_pricing).
    line_items, subtotal = price_cart_items(order_in.items)
    delivery_cost = compute_delivery_fee(order_in.delivery_type)
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

    # 3. Conversación: si el carrito trae un token de sesión válido (generado por el bot al
    #    mandar el enlace de /menu, ver webhooks._send_branch_welcome_and_menu), se usa ESA
    #    conversationId directamente — es determinista y evita el caso donde la búsqueda por
    #    teléfono encuentra una conversación distinta a la que el agente tiene abierta (por
    #    ejemplo si el contacto tiene más de una conversación "abierta" a la vez). Si no hay
    #    sesión válida (enlace viejo, o cliente entrando a /menu sin pasar por el bot), se cae
    #    al comportamiento histórico de buscar/crear por teléfono.
    conv = None
    is_new_conv = False
    session_data = decode_menu_session_token(order_in.session) if order_in.session else None
    if session_data:
        conv = db.query(Conversation).filter(
            Conversation.id == session_data["conv"],
            Conversation.deleted_at.is_(None),
        ).first()

    if not conv:
        conv = db.query(Conversation).filter(
            Conversation.customer_id == contact.id,
            Conversation.status.in_(["new", "unassigned", "open", "pending"]),
            Conversation.deleted_at.is_(None)
        ).order_by(Conversation.updated_at.desc()).first()

    if not conv:
        conv = Conversation(customer_id=contact.id, status="unassigned", created_at=now, updated_at=now)
        db.add(conv)
        db.flush()
        is_new_conv = True
    elif conv.customer_id != contact.id:
        # La sesión apunta a una conversación real, pero de OTRO contacto (teléfono cambiado a
        # mano en el formulario, por ejemplo): no se reasigna la conversación de otra persona,
        # se cae al flujo por teléfono para no filtrar datos entre clientes.
        conv = db.query(Conversation).filter(
            Conversation.customer_id == contact.id,
            Conversation.status.in_(["new", "unassigned", "open", "pending"]),
            Conversation.deleted_at.is_(None)
        ).order_by(Conversation.updated_at.desc()).first()
        if not conv:
            conv = Conversation(customer_id=contact.id, status="unassigned", created_at=now, updated_at=now)
            db.add(conv)
            db.flush()
            is_new_conv = True

    conv.branch_id = branch.id
    conv.delivery_type = order_in.delivery_type
    conv.payment_method = order_in.payment_method
    conv.updated_at = now

    # 4. Registrar la comanda: si ya existe un carrito activo sincronizado para esta conversación
    #    (Puntos 12 y 17), se confirma ESA misma fila en vez de crear un pedido nuevo — así el
    #    panel nunca ve pedidos duplicados por la misma compra.
    active_cart = get_active_cart(db, conv.id)
    order_code = f"FH-{uuid.uuid4().hex[:6].upper()}"
    order_type = "takeout" if order_in.delivery_type == "pickup" else "delivery"
    items_payload = json.dumps({
        "items": line_items,
        "delivery_address": order_in.delivery_address,
        "payment_method": order_in.payment_method,
        "source": "menu_web",
    }, ensure_ascii=False)

    if active_cart:
        order = active_cart
        order.order_code = order_code
        order.branch_id = branch.id
        order.order_type = order_type
        order.status = "en_proceso"
        order.subtotal = subtotal
        order.delivery_cost = delivery_cost
        order.total = total
        order.items_json = items_payload
        order.updated_at = now
        order.expires_at = None
    else:
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
            items_json=items_payload,
            created_by=None,
            created_at=now,
            updated_at=now,
        )
        db.add(order)

    db.commit()
    db.refresh(order)
    db.refresh(conv)

    # 5. Construir el mensaje estructurado que el cliente confirmará en WhatsApp
    whatsapp_text = _build_whatsapp_order_text(order_code, branch.name, line_items, order_in, delivery_cost, total)
    whatsapp_url = f"https://wa.me/{whatsapp_destination}?text={quote(whatsapp_text)}"

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


@router.put("/cart", response_model=CartSyncResponse)
async def sync_cart(
    cart_in: CartSyncRequest,
    db: Session = Depends(get_db)
):
    """
    Endpoint público (sin autenticación) usado por /menu para sincronizar el carrito del
    cliente en tiempo real con el panel administrativo, mientras el cliente todavía está
    armando su pedido (Puntos 1, 5 y 6 del pedido del usuario).

    Requiere un token de sesión de menú válido (`session`, ver security.auth.
    create_menu_session_token): sin él no hay forma segura de saber a qué conversación
    pertenece el carrito, así que la petición se rechaza con 401 en vez de confiar en un
    conversationId que el cliente podría editar a mano en la URL (Punto 8).
    """
    session_data = decode_menu_session_token(cart_in.session)
    if not session_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión de menú inválida o expirada. Vuelve a abrir el enlace del menú desde WhatsApp.")

    conv = db.query(Conversation).filter(
        Conversation.id == session_data["conv"],
        Conversation.deleted_at.is_(None),
    ).first()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada.")

    branch = None
    if cart_in.branch_code:
        branch = db.query(Branch).filter(Branch.code == cart_in.branch_code, Branch.active == True).first()
    if not branch and conv.branch_id:
        branch = db.query(Branch).filter(Branch.id == conv.branch_id).first()
    if not branch:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sucursal inválida o inactiva.")

    if cart_in.delivery_type == "delivery" and not (cart_in.delivery_address or "").strip():
        # El carrito puede sincronizarse antes de que el cliente escriba la dirección (por
        # ejemplo justo al tocar "Delivery"); no es un error, simplemente aún no hay dirección.
        cart_in.delivery_address = None

    # Carrito vacío (cliente eliminó el último producto): se borra el borrador en vez de dejar
    # una fila con subtotal $0.00 dando vueltas (Punto 12).
    if not cart_in.items:
        clear_active_cart(db, conv.id)
        await ws_manager.broadcast_to_branch(branch.id, {
            "type": "cart_update",
            "conversation_id": conv.id,
            "branch_id": branch.id,
            "cart": cart_to_dict(None),
        })
        return CartSyncResponse(
            conversation_id=conv.id, status="empty", items=[],
            subtotal=Decimal("0.00"), delivery_fee=Decimal("0.00"), total=Decimal("0.00"),
        )

    line_items, subtotal = price_cart_items(cart_in.items)
    delivery_fee = compute_delivery_fee(cart_in.delivery_type)
    total = (subtotal + delivery_fee).quantize(Decimal("0.01"))

    now = datetime.now(timezone.utc)
    conv.branch_id = branch.id
    conv.delivery_type = cart_in.delivery_type
    if cart_in.payment_method:
        conv.payment_method = cart_in.payment_method
    conv.updated_at = now

    cart = upsert_active_cart(
        db,
        conversation_id=conv.id,
        branch_id=branch.id,
        line_items=line_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        delivery_type=cart_in.delivery_type,
        delivery_address=cart_in.delivery_address,
        payment_method=cart_in.payment_method,
    )
    db.commit()

    await ws_manager.broadcast_to_branch(branch.id, {
        "type": "cart_update",
        "conversation_id": conv.id,
        "branch_id": branch.id,
        "cart": cart_to_dict(cart),
    })

    return CartSyncResponse(
        conversation_id=conv.id,
        status=cart.status,
        items=line_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
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

