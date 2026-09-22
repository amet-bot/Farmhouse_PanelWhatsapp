from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List
from datetime import datetime


class InventoryItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    unit: str = Field(..., min_length=1, max_length=30)
    category: Optional[str] = Field(None, max_length=50)


class InventoryItemResponse(BaseModel):
    id: int
    name: str
    unit: str
    category: Optional[str] = None
    active: bool

    model_config = ConfigDict(from_attributes=True)


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    phone: Optional[str] = Field(None, max_length=30)


class SupplierResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ShipmentItemCreate(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)


class ShipmentItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class ShipmentCreate(BaseModel):
    branch_id: int
    received_at: Optional[datetime] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    items: List[ShipmentItemCreate] = Field(..., min_length=1, max_length=100)


class ShipmentResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    received_by_user_id: int
    received_by_name: str
    received_at: datetime
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    items: List[ShipmentItemResponse]
    total_cost: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


# ==========================================================================
# Merma y existencias
# ==========================================================================
class WasteItemCreate(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0)
    # Normalmente no se manda: el backend lo copia del último cargamento de ese insumo en esa
    # sucursal. Se acepta por si quien carga sabe que ese lote costó otra cosa.
    unit_cost: Optional[Decimal] = Field(None, ge=0)


class WasteItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    # Existencia que quedaba de ese insumo en esa sucursal justo antes de este registro. Se
    # calcula al responder, no se guarda: sirve para avisar "esto deja el stock en negativo".
    stock_before: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class WasteCreate(BaseModel):
    branch_id: int
    reason: str = Field(..., min_length=1, max_length=40)
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = None
    items: List[WasteItemCreate] = Field(..., min_length=1, max_length=100)


class WasteResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    recorded_by_user_id: int
    recorded_by_name: str
    occurred_at: datetime
    reason: str
    reason_label: str
    notes: Optional[str] = None
    created_at: datetime
    items: List[WasteItemResponse]
    total_cost: Optional[Decimal] = None
    # Insumos de este registro que dejaron la existencia por debajo de cero. No impide guardar
    # (ver create_waste): el sistema empezó a contar entradas hace poco y nadie cargó el
    # inventario de arranque, así que un negativo dice "falta cargar el arranque", no "error".
    negative_items: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class WasteReasonResponse(BaseModel):
    code: str
    label: str


class StockRowResponse(BaseModel):
    """Una fila de existencias: un insumo en una sucursal."""
    inventory_item_id: int
    item_name: str
    unit: str
    category: Optional[str] = None
    branch_id: Optional[int] = None      # None cuando la fila es el total de todas las sucursales
    branch_name: Optional[str] = None
    entered: Decimal                     # todo lo que entró por cargamentos
    wasted: Decimal                      # todo lo que salió por merma
    on_hand: Decimal                     # entered - wasted; puede ser negativo, a propósito
    wasted_cost: Optional[Decimal] = None
    last_movement_at: Optional[datetime] = None
    # Costo unitario del último cargamento de ese insumo en esa sucursal. Solo viaja cuando la
    # consulta pide UNA sucursal: sumando todas no existe "el último costo", existe uno por
    # sucursal, y elegir cualquiera sería inventar. Lo usa el formulario de merma para estimar
    # la pérdida antes de guardar; el número que vale es el que calcula el servidor al grabar.
    last_unit_cost: Optional[Decimal] = None
