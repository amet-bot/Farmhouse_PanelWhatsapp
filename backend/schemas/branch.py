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

class BranchCreate(BranchBase):
    pass

class BranchResponse(BranchBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

