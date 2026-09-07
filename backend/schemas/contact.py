from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

class ContactBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    phone: str = Field(..., min_length=4, max_length=50)
    avatar_url: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)
    address: Optional[str] = Field(None, max_length=500)
    building_or_house: Optional[str] = Field(None, max_length=150)
    floor_or_unit: Optional[str] = Field(None, max_length=100)
    address_reference: Optional[str] = Field(None, max_length=300)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

class ContactCreate(ContactBase):
    pass

class ContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    notes: Optional[str] = Field(None, max_length=5000)
    avatar_url: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    building_or_house: Optional[str] = Field(None, max_length=150)
    floor_or_unit: Optional[str] = Field(None, max_length=100)
    address_reference: Optional[str] = Field(None, max_length=300)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

class ContactResponse(ContactBase):
    id: int
    created_at: datetime
    last_interaction: datetime

    model_config = ConfigDict(from_attributes=True)

