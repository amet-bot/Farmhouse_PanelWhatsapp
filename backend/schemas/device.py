from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum
from schemas.branch import BranchResponse
from schemas.user import UserResponse

class DeviceTypeEnum(str, Enum):
    computadora = "computadora"
    tablet = "tablet"
    celular = "celular"

class DeviceStatusEnum(str, Enum):
    active = "active"
    disabled = "disabled"
    revoked = "revoked"

class DeviceBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    device_type: DeviceTypeEnum
    branch_id: int
    assigned_user_id: Optional[int] = None
    ip_address: Optional[str] = Field(None, max_length=50)
    status: Optional[DeviceStatusEnum] = DeviceStatusEnum.active

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v

class DeviceCreate(DeviceBase):
    active: Optional[bool] = True

class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    device_type: Optional[DeviceTypeEnum] = None
    branch_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    status: Optional[DeviceStatusEnum] = None
    active: Optional[bool] = None
    ip_address: Optional[str] = Field(None, max_length=50)

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v

class DeviceResponse(DeviceBase):
    id: int
    device_id: str
    last_seen: Optional[datetime] = None
    created_at: datetime
    branch: Optional[BranchResponse] = None
    assigned_user: Optional[UserResponse] = None
    active: Optional[bool] = True

    model_config = ConfigDict(from_attributes=True)

