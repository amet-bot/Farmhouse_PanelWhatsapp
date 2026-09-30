from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

class BranchBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    code: str = Field(..., min_length=2, max_length=50)
    color: Optional[str] = Field("#16a34a", pattern="^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
    active: Optional[bool] = True
    address: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    accepts_delivery: bool = True
    # Horario de atención "HH:MM" (hora de Panamá); vacío = horario general 10:30 a 21:30.
    opens_at: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closes_at: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

class BranchCreate(BranchBase):
    pass

class BranchUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    code: Optional[str] = Field(None, min_length=2, max_length=50)
    color: Optional[str] = Field(None, pattern="^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
    address: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    accepts_delivery: Optional[bool] = None
    opens_at: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closes_at: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

class BranchResponse(BranchBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

