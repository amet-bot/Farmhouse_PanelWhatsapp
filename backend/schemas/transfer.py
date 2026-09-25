from pydantic import BaseModel, Field, ConfigDict
from decimal import Decimal
from typing import Optional, List
from datetime import datetime


class TransferItemCreate(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)


class TransferCreate(BaseModel):
    from_branch_id: int
    to_branch_id: int
    notes: Optional[str] = None
    items: List[TransferItemCreate] = Field(..., min_length=1, max_length=200)


class TransferItemResponse(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    quantity: Decimal
    unit_cost: Optional[Decimal] = None


class TransferResponse(BaseModel):
    id: int
    from_branch_id: int
    from_branch_name: str
    to_branch_id: int
    to_branch_name: str
    status: str
    requested_by_user_id: int
    requested_by_name: str
    approved_by_user_id: Optional[int] = None
    dispatched_by_user_id: Optional[int] = None
    received_by_user_id: Optional[int] = None
    requested_at: datetime
    approved_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    items: List[TransferItemResponse]

    model_config = ConfigDict(from_attributes=True)


class TransferActionRequest(BaseModel):
    """Body opcional para approve/reject/cancel — la nota queda como motivo/comentario del paso."""
    notes: Optional[str] = None
