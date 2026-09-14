from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict


class BotFlowResponse(BaseModel):
    key: str
    name: str
    graph: Dict[str, Any]
    updated_at: datetime
    updated_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BotFlowUpdate(BaseModel):
    # El diagrama completo: {"nodes": [...], "conns": [...]}. Sin validación estructural
    # estricta a propósito — el editor del panel es la única fuente de escritura hoy, y
    # exigir un esquema rígido aquí solo estorbaría mientras el formato del grafo todavía
    # puede evolucionar.
    graph: Dict[str, Any]
