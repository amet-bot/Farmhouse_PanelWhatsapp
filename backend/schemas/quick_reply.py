import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator

SHORTCUT_RE = re.compile(r"^[a-z0-9_\-]{1,40}$")


class QuickReplyBase(BaseModel):
    shortcut: str = Field(..., min_length=1, max_length=40)
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=4000)
    branch_id: Optional[int] = None
    active: bool = True
    sort_order: int = 0

    @field_validator("shortcut", mode="before")
    @classmethod
    def normalize_shortcut(cls, v):
        # Se escribe "/horario": sin barra, minúsculas, sin espacios ni tildes para que se pueda
        # teclear rápido en una tablet.
        if not isinstance(v, str):
            return v
        s = v.strip().lstrip("/").lower().replace(" ", "_")
        s = (s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
        if not SHORTCUT_RE.match(s):
            raise ValueError("El atajo solo puede tener letras, números, guion o guion bajo (ej: horario, menu_cde).")
        return s

    @field_validator("title", "body", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v


class QuickReplyCreate(QuickReplyBase):
    pass


class QuickReplyUpdate(BaseModel):
    shortcut: Optional[str] = Field(None, min_length=1, max_length=40)
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    body: Optional[str] = Field(None, min_length=1, max_length=4000)
    branch_id: Optional[int] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None

    _norm = field_validator("shortcut", mode="before")(QuickReplyBase.normalize_shortcut.__func__)
    _strip = field_validator("title", "body", mode="before")(QuickReplyBase.strip_text.__func__)


class QuickReplyResponse(QuickReplyBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
