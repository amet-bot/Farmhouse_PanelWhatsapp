from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum

class MessageDirectionEnum(str, Enum):
    incoming = "incoming"
    outgoing = "outgoing"

class MessageSenderTypeEnum(str, Enum):
    customer = "customer"
    agent = "agent"
    system = "system"

class MessageStatusEnum(str, Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"

class MessageBase(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    is_internal: Optional[bool] = False

class MessageCreate(MessageBase):
    conversation_id: int

class MessageResponse(MessageBase):
    id: int
    conversation_id: int
    direction: str
    sender_type: str
    sender_id: Optional[int] = None
    whatsapp_message_id: Optional[str] = None
    status: str = "sent"
    error_detail: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    media_mime_type: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

