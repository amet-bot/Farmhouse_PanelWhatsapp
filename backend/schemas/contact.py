from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

class ContactBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    phone: str = Field(..., min_length=4, max_length=50)
    avatar_url: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)

class ContactCreate(ContactBase):
    pass

class ContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    notes: Optional[str] = Field(None, max_length=5000)
    avatar_url: Optional[str] = Field(None, max_length=255)

class ContactResponse(ContactBase):
    id: int
    created_at: datetime
    last_interaction: datetime

    model_config = ConfigDict(from_attributes=True)

