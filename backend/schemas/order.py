from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List
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

class OrderResponse(OrderBase):
    id: int
    order_code: str
    conversation_id: int
    branch_id: int
    status: str
    created_by: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

