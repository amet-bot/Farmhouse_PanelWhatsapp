from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


# ==========================================================================
# Solicitudes de insumos
# ==========================================================================
class SupplyRequestCreate(BaseModel):
    branch_id: int
    item_name: str = Field(..., min_length=1, max_length=150)
    quantity_hint: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None
    # Opcionales: ligar la solicitud a un insumo del catálogo con cantidad (la cuenta el pedido sugerido).
    inventory_item_id: Optional[int] = None
    quantity: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=3)


class SupplyRequestResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    requested_by_user_id: int
    requested_by_name: str
    item_name: str
    quantity_hint: Optional[str] = None
    inventory_item_id: Optional[int] = None
    quantity: Optional[Decimal] = None
    notes: Optional[str] = None
    status: str
    created_at: datetime
    approved_by_user_id: Optional[int] = None
    approved_by_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    resolved_by_user_id: Optional[int] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None


# ==========================================================================
# Incidencias
# ==========================================================================
class IncidentCreate(BaseModel):
    branch_id: int
    title: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    severity: str = "media"


class IncidentStatusUpdate(BaseModel):
    status: str
    resolution_notes: Optional[str] = None


class IncidentAssign(BaseModel):
    user_id: Optional[int] = None   # None = quitar la asignación


class IncidentResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    reported_by_user_id: int
    reported_by_name: str
    assigned_to_user_id: Optional[int] = None
    assigned_to_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    created_at: datetime
    resolved_by_user_id: Optional[int] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    hours_open: Optional[float] = None   # horas desde que se reportó, si sigue sin resolver


# ==========================================================================
# Tareas
# ==========================================================================
class TaskCreate(BaseModel):
    branch_id: int
    title: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    due_date: Optional[datetime] = None
    requires_photo: bool = False


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=150)
    description: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    clear_assignee: bool = False
    due_date: Optional[datetime] = None
    clear_due_date: bool = False
    requires_photo: Optional[bool] = None


class TaskStatusUpdate(BaseModel):
    status: str


class TaskPhotoOut(BaseModel):
    id: int
    created_at: datetime
    uploaded_by_name: Optional[str] = None


class TaskResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    created_by_user_id: int
    created_by_name: str
    assigned_to_user_id: Optional[int] = None
    assigned_to_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: str
    due_date: Optional[datetime] = None
    overdue: bool = False
    created_at: datetime
    completed_at: Optional[datetime] = None
    requires_photo: bool = False
    photos: List[TaskPhotoOut] = []


# ==========================================================================
# Tareas recurrentes
# ==========================================================================
class RecurringTaskCreate(BaseModel):
    branch_id: Optional[int] = None   # None = cada local (todas las sucursales activas)
    title: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    frequency: str = "daily"
    times: List[str] = Field(..., min_length=1, max_length=10)   # ["07:00"] o ["08:00","15:00","20:00"]
    day_of_month: Optional[int] = Field(None, ge=1, le=31)   # requerido si frequency="monthly"


class RecurringTaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=150)
    description: Optional[str] = None
    times: Optional[List[str]] = Field(None, min_length=1, max_length=10)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    active: Optional[bool] = None


class RecurringTaskResponse(BaseModel):
    id: int
    branch_id: Optional[int] = None
    branch_name: Optional[str] = None   # None cuando es "cada local"
    created_by_user_id: int
    created_by_name: str
    title: str
    description: Optional[str] = None
    frequency: str
    times: List[str]
    day_of_month: Optional[int] = None
    active: bool
    created_at: datetime


# ==========================================================================
# Centro de operación
# ==========================================================================
class TeamMember(BaseModel):
    id: int
    name: str
    role: str
    branch_id: Optional[int] = None


class BranchOverview(BaseModel):
    branch_id: int
    branch_name: str
    branch_code: str
    incidents_open: int = 0
    incidents_alta: int = 0
    tasks_pending: int = 0
    tasks_overdue: int = 0
    requests_open: int = 0
    requests_approved: int = 0
    transfers_to_approve: int = 0
    transfers_to_dispatch: int = 0
    transfers_to_receive: int = 0
    expected_today: int = 0
    expected_overdue: int = 0
    prep_checkpoints_today: int = 0
    prep_filled_today: int = 0
    days_since_count: Optional[int] = None
    unassigned_conversations: int = 0
    attention: int = 0   # puntaje para ordenar: lo grave y lo vencido pesan más


class OpsOverviewResponse(BaseModel):
    generated_at: datetime
    branches: List[BranchOverview]
    totals: BranchOverview
