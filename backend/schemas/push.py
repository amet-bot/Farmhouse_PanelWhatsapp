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
