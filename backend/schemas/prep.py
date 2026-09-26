from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional, List
from datetime import date, datetime


class PrepTemplateItemIn(BaseModel):
    # Al editar, el id del ítem existente: así se actualiza en su lugar y el historial de
    # checklists sigue apuntando al mismo ítem. Ausente = ítem nuevo.
    id: Optional[int] = None
    section: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=150)
    unit_label: Optional[str] = Field(None, max_length=60)
    par_target: Optional[Decimal] = Field(None, ge=0)
    notes: Optional[str] = None


class PrepTemplateCreate(BaseModel):
    branch_id: int
    name: str = Field("Bowls", min_length=1, max_length=100)
    checkpoints: List[str] = Field(..., min_length=1, max_length=10)
    items: List[PrepTemplateItemIn] = Field(default_factory=list, max_length=300)


class PrepTemplateItemResponse(BaseModel):
    id: int
    section: str
    name: str
    unit_label: Optional[str] = None
    par_target: Optional[Decimal] = None
    notes: Optional[str] = None
    sort_order: int


class PrepTemplateResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    name: str
    checkpoints: List[str]
    items: List[PrepTemplateItemResponse]
    created_at: datetime
    updated_at: Optional[datetime] = None


class PrepTemplateSummary(BaseModel):
    """Versión liviana para listar plantillas sin traer todos los ítems."""
    id: int
    branch_id: int
    branch_name: str
    name: str
    checkpoints: List[str]
    item_count: int


# ==========================================================================
# Checklist diario
# ==========================================================================
class PrepCheckEntryIn(BaseModel):
    template_item_id: int
    on_hand: Decimal = Field(..., ge=0)
    note: Optional[str] = None


class PrepCheckSubmit(BaseModel):
    checkpoint: str = Field(..., min_length=1, max_length=60)
    check_date: Optional[date] = None
    entries: List[PrepCheckEntryIn] = Field(..., min_length=1, max_length=300)


class PrepCheckEntryResponse(BaseModel):
    template_item_id: int
    item_name: str
    section: str
    unit_label: Optional[str] = None
    par_target: Optional[Decimal] = None
    on_hand: Decimal
    note: Optional[str] = None
    ready: bool


class PrepCheckResponse(BaseModel):
    id: int
    template_id: int
    branch_id: int
    checkpoint: str
    check_date: date
    filled_by_user_id: int
    filled_by_name: str
    entries: List[PrepCheckEntryResponse]
    created_at: datetime
    updated_at: Optional[datetime] = None
