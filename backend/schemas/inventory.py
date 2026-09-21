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
