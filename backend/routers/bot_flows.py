import json
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models.bot_flow import BotFlow
from models.user import User
from schemas.bot_flow import BotFlowResponse, BotFlowUpdate
from security.auth import get_current_user, require_role

logger = logging.getLogger("farmhouse.bot_flows")

router = APIRouter(prefix="/bot-flows", tags=["Flujo Visual del Bot"])


def _to_response(flow: BotFlow, db: Session) -> BotFlowResponse:
    updated_by_name = None
    if flow.updated_by_id:
        u = db.query(User).filter(User.id == flow.updated_by_id).first()
        updated_by_name = u.name if u else None
    return BotFlowResponse(
        key=flow.key,
        name=flow.name,
        graph=json.loads(flow.graph_json),
        updated_at=flow.updated_at,
        updated_by=updated_by_name,
    )


@router.get("/{key}", response_model=BotFlowResponse)
def get_bot_flow(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lectura de solo consulta: cualquier usuario autenticado puede VER el diagrama (por
    ejemplo, para entender cómo funciona el bot), pero editarlo requiere admin (ver PUT).
    """
    flow = db.query(BotFlow).filter(BotFlow.key == key).first()
    if not flow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe un flujo con esa clave.")
    return _to_response(flow, db)


@router.put("/{key}", response_model=BotFlowResponse, dependencies=[Depends(require_role(["admin"]))])
def save_bot_flow(
    key: str,
    payload: BotFlowUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Guarda el diagrama completo, reemplazando el anterior. Solo admin: este panel controla
    el contenido que el equipo usará como referencia del flujo, y (más adelante, si se
    decide conectar el motor real) lo que el bot le contestaría a un cliente real.
    """
    flow = db.query(BotFlow).filter(BotFlow.key == key).first()
    if not flow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe un flujo con esa clave.")

    nodes = payload.graph.get("nodes")
    if not isinstance(nodes, list) or not any(n.get("type") == "trigger" for n in nodes if isinstance(n, dict)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El flujo necesita al menos un nodo disparador para poder guardarse.",
        )

    flow.graph_json = json.dumps(payload.graph, ensure_ascii=False)
    flow.updated_by_id = current_user.id
    db.commit()
    db.refresh(flow)
    logger.info(f"[BotFlow] '{key}' actualizado por @{current_user.username} ({len(nodes)} nodos).")
    return _to_response(flow, db)
