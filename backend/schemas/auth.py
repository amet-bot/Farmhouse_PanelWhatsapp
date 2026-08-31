from pydantic import BaseModel, field_validator, ConfigDict
from typing import Optional
from schemas.user import UserResponse

class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator('username', mode='before')
    @classmethod
    def sanitize_username(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    ws_ticket: Optional[str] = None
    user: UserResponse

    model_config = ConfigDict(from_attributes=True)

class TokenPayload(BaseModel):
    sub: Optional[str] = None
    jti: Optional[str] = None
    iat: Optional[int] = None
    exp: Optional[int] = None

