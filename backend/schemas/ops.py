from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ==========================================================================
# Solicitudes de insumos
# ==========================================================================
class SupplyRequestCreate(BaseModel):
    branch_id: int
    item_name: str = Field(..., min_length=1, max_length=150)
    quantity_hint: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class SupplyRequestResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    requested_by_user_id: int
    requested_by_name: str
    item_name: str
    quantity_hint: Optional[str] = None
    notes: Optional[str] = None
    status: str
    created_at: datetime
    resolved_by_user_id: Optional[int] = None
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


class IncidentResponse(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    reported_by_user_id: int
    reported_by_name: str
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    created_at: datetime
    resolved_by_user_id: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None


# ==========================================================================
# Tareas
# ==========================================================================
class TaskCreate(BaseModel):
    branch_id: int
    title: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    due_date: Optional[datetime] = None


class TaskStatusUpdate(BaseModel):
    status: str


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
    created_at: datetime
    completed_at: Optional[datetime] = None
