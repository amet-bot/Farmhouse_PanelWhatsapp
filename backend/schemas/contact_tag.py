import re
from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, ConfigDict, field_validator

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ContactTagBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str = Field("#16a34a", min_length=7, max_length=7)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("color")
    @classmethod
    def valid_color(cls, v):
        if not COLOR_RE.match(v or ""):
            raise ValueError("El color debe ser #rrggbb.")
        return v.lower()


class ContactTagCreate(ContactTagBase):
    pass


class ContactTagUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    color: str | None = Field(None, min_length=7, max_length=7)

    _strip = field_validator("name", mode="before")(ContactTagBase.strip_name.__func__)
    _color = field_validator("color")(ContactTagBase.valid_color.__func__)


class ContactTagResponse(ContactTagBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContactTagsAssign(BaseModel):
    """Reemplaza el conjunto completo de etiquetas del cliente (lo que quedó marcado en el selector)."""
    tag_ids: List[int] = Field(default_factory=list, max_length=50)
