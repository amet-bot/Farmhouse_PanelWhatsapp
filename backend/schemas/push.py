from pydantic import BaseModel, Field
from typing import Optional

class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=255)
    auth: str = Field(..., min_length=1, max_length=255)

class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=500)
    keys: PushSubscriptionKeys
    user_agent: Optional[str] = Field(None, max_length=255)

class PushUnsubscribe(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=500)


class NativeTokenIn(BaseModel):
    """El token de Firebase de un celular con la app de Android."""
    token: str = Field(..., min_length=20, max_length=255)
    platform: str = Field("android", pattern="^(android|ios)$")
