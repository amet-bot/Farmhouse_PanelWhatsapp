from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from schemas.contact import ContactResponse
from schemas.branch import BranchResponse
from schemas.user import UserResponse
from schemas.message import MessageResponse
from schemas.order import OrderResponse

class ConversationBase(BaseModel):
    customer_id: int
    branch_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    status: Optional[str] = "new"
    delivery_type: Optional[str] = None
    payment_method: Optional[str] = None
    automation_paused: Optional[bool] = False

class ConversationCreate(ConversationBase):
    pass

class ConversationTransferRequest(BaseModel):
    target_branch_id: int
    reason: Optional[str] = Field(None, max_length=500)

class ConversationResponse(ConversationBase):
    id: int
    created_at: datetime
    updated_at: datetime
    contact: Optional[ContactResponse] = None
    branch: Optional[BranchResponse] = None
    assigned_user: Optional[UserResponse] = None
    messages: List[MessageResponse] = Field(default_factory=list)
    orders: List[OrderResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

