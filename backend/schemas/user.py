from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum
from schemas.branch import BranchResponse

class UserRoleEnum(str, Enum):
    admin = "admin"
    supervisor = "supervisor"
    agent = "agent"

class UserBase(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2, max_length=150)
    email: Optional[str] = Field(None, max_length=150)
    role: UserRoleEnum = UserRoleEnum.agent
    branch_id: Optional[int] = None
    avatar_url: Optional[str] = Field(None, max_length=255)
    active: Optional[bool] = True

    @field_validator('username', mode='before')
    @classmethod
    def sanitize_username(cls, v):
        if isinstance(v, str):
            v_clean = v.strip().lower()
            if len(v_clean) < 2:
                raise ValueError("El nombre de usuario debe tener al menos 2 caracteres.")
            return v_clean
        return v

    @field_validator('email', mode='before')
    @classmethod
    def sanitize_email(cls, v):
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
        return None

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v

class UserCreate(UserBase):
    password: str = Field(..., min_length=4, max_length=100)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if not v or len(v.strip()) < 4:
            raise ValueError("La contraseña debe tener al menos 4 caracteres.")
        return v.strip()

class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=2, max_length=50)
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    email: Optional[str] = Field(None, max_length=150)
    role: Optional[UserRoleEnum] = None
    branch_id: Optional[int] = None
    avatar_url: Optional[str] = Field(None, max_length=255)
    active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=4, max_length=100)

    @field_validator('username', mode='before')
    @classmethod
    def sanitize_username(cls, v):
        if isinstance(v, str) and v.strip():
            v_clean = v.strip().lower()
            if len(v_clean) < 2:
                raise ValueError("El nombre de usuario debe tener al menos 2 caracteres.")
            return v_clean
        return v

    @field_validator('email', mode='before')
    @classmethod
    def sanitize_email(cls, v):
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
        return None

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        if isinstance(v, str) and v.strip():
            return v.strip()
        return v

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if v is not None and len(v.strip()) < 10:
            raise ValueError("La contraseña debe tener al menos 10 caracteres.")
        return v.strip() if v else None

class UserResponse(UserBase):
    id: int
    created_at: datetime
    branch: Optional[BranchResponse] = None

    model_config = ConfigDict(from_attributes=True)

