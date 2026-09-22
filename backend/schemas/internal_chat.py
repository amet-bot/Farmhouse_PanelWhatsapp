from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime


class InternalUserBrief(BaseModel):
    """Cómo se ve una persona en el directorio y como contraparte de un hilo."""
    id: int
    name: str
    role: str
    branch_id: Optional[int] = None
    branch_name: Optional[str] = None
    online: bool = False

    model_config = ConfigDict(from_attributes=True)


class InternalMessageResponse(BaseModel):
    id: int
    thread_id: int
    sender_user_id: int
    sender_name: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InternalMessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class InternalThreadResponse(BaseModel):
    id: int
    kind: str                      # "direct" | "branch"
    title: str                     # nombre de la contraparte, o "Equipo <sucursal>"
    subtitle: Optional[str] = None # sucursal y rol de la contraparte, o cantidad de integrantes
    branch_id: Optional[int] = None
    counterpart: Optional[InternalUserBrief] = None  # solo en los directos
    member_count: Optional[int] = None               # solo en los canales
    last_message_at: Optional[datetime] = None
    last_message_preview: Optional[str] = None
    last_message_sender: Optional[str] = None
    unread_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class DirectThreadCreate(BaseModel):
    user_id: int


class InternalThreadDetail(InternalThreadResponse):
    members: List[InternalUserBrief] = []
