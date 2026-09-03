from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List, Literal
from datetime import datetime
from enum import Enum

class OrderTypeEnum(str, Enum):
    delivery = "delivery"
    takeout = "takeout"
    catering = "catering"

class OrderStatusEnum(str, Enum):
    en_proceso = "en_proceso"
    en_cocina = "en_cocina"
    en_delivery = "en_delivery"
    entregado = "entregado"
    cancelado = "cancelado"

class OrderItemSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    quantity: int = Field(1, ge=1)
    price: Decimal = Field(..., ge=0)
    notes: Optional[str] = None

class OrderBase(BaseModel):
    order_type: OrderTypeEnum = OrderTypeEnum.delivery
    subtotal: Decimal = Field(Decimal("0.00"), ge=0)
    delivery_cost: Decimal = Field(Decimal("0.00"), ge=0)
    tax: Decimal = Field(Decimal("0.00"), ge=0)
    total: Decimal = Field(Decimal("0.00"), ge=0)
    items_json: Optional[str] = None

class OrderCreate(BaseModel):
    conversation_id: int
    branch_id: int
    order_type: OrderTypeEnum = OrderTypeEnum.delivery
    subtotal: Decimal = Field(..., ge=0)
    delivery_cost: Decimal = Field(Decimal("0.00"), ge=0)
    items: Optional[List[OrderItemSchema]] = None
    items_json: Optional[str] = None

class OrderUpdate(BaseModel):
    status: Optional[OrderStatusEnum] = None
    order_type: Optional[OrderTypeEnum] = None
    subtotal: Optional[Decimal] = Field(None, ge=0)
    delivery_cost: Optional[Decimal] = Field(None, ge=0)
    items_json: Optional[str] = None

class PublicOrderItem(BaseModel):
    sku: str = Field(..., min_length=1, max_length=60)
    quantity: int = Field(1, ge=1, le=20)
    addon_skus: List[str] = Field(default_factory=list, max_length=15)
    notes: Optional[str] = Field(None, max_length=300)


class PublicOrderCreate(BaseModel):
    branch_code: str = Field(..., min_length=1, max_length=20)
    delivery_type: Literal["pickup", "delivery"]
    delivery_address: Optional[str] = Field(None, max_length=300)
    payment_method: Literal["yappy", "ach", "card", "cash"]
    customer_name: str = Field(..., min_length=2, max_length=100)
    customer_phone: str = Field(..., min_length=6, max_length=20)
    items: List[PublicOrderItem] = Field(..., min_length=1, max_length=50)
    # Número de WhatsApp de origen (parámetro ?wa= de /menu, ver webhooks._send_branch_welcome_and_menu).
    # Nunca se confía en este valor a ciegas: el backend solo lo usa si coincide exactamente con
    # el número oficial de Farmhouse (get_official_whatsapp_number); de lo contrario se ignora.
    origin_wa: Optional[str] = Field(None, max_length=20)
    # Token de sesión del Menú Digital (ver security.auth.create_menu_session_token). Si viene y
    # es válido, el pedido se ata EXACTAMENTE a esa conversationId (y convierte el carrito activo
    # ya sincronizado en el pedido confirmado, en vez de crear una fila nueva) en lugar de resolver
    # la conversación por teléfono, que puede fallar si hay más de una conversación abierta para
    # el mismo contacto. Opcional por compatibilidad con enlaces /menu antiguos sin `session`.
    session: Optional[str] = Field(None, max_length=2000)


class PublicOrderResponse(BaseModel):
    order_code: str
    conversation_id: int
    subtotal: Decimal
    delivery_cost: Decimal
    total: Decimal
    whatsapp_url: str


class CartItemIn(BaseModel):
    sku: str = Field(..., min_length=1, max_length=60)
    quantity: int = Field(1, ge=1, le=20)
    addon_skus: List[str] = Field(default_factory=list, max_length=15)
    notes: Optional[str] = Field(None, max_length=300)


class CartSyncRequest(BaseModel):
    """
    Snapshot del carrito del cliente en /menu, enviado en cada cambio (agregar/quitar producto,
    cambiar cantidad, delivery/retiro, método de pago...). `session` es obligatorio: sin un
    token de sesión de menú válido no se sabe a qué conversación pertenece el carrito y la
    sincronización se ignora (Punto 8: nunca confiar en un conversationId de la URL a secas).
    """
    session: str = Field(..., min_length=10, max_length=2000)
    items: List[CartItemIn] = Field(default_factory=list, max_length=50)
    delivery_type: Literal["pickup", "delivery"] = "pickup"
    delivery_address: Optional[str] = Field(None, max_length=300)
    payment_method: Optional[Literal["yappy", "ach", "card", "cash"]] = None
    branch_code: Optional[str] = Field(None, max_length=20)


class CartSyncResponse(BaseModel):
    conversation_id: int
    status: str
    items: list
    subtotal: Decimal
    delivery_fee: Decimal
    total: Decimal


class OrderResponse(OrderBase):
    id: int
    order_code: str
    conversation_id: int
    branch_id: int
    status: str
    created_by: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

