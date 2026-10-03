"""
Sistema: respaldos de la base de datos (services/db_backup.py). Solo admin (permiso system.backup).
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from models.user import User
from security.permissions import require_permission
from services import db_backup
from services.audit import log_audit_event
from database import get_db

logger = logging.getLogger("farmhouse.system")

router = APIRouter(prefix="/system", tags=["Sistema"])


def _audit(db: Session, user: User, action: str, name: str) -> None:
    log_audit_event(db, user.id, None, action, "backup", None, {"name": name})
    db.commit()


@router.get("/backups")
def list_backups(current_user: User = Depends(require_permission("system.backup"))):
    return {
        "supported": db_backup.is_supported(),
        "keep_days": db_backup.KEEP_DAYS,
        "schedule": f"Todos los días a las {db_backup.RUN_HOUR}:{db_backup.RUN_MINUTE:02d} a. m.",
        "backups": db_backup.list_backups(),
    }


@router.post("/backups", status_code=status.HTTP_201_CREATED)
async def create_backup(db: Session = Depends(get_db), current_user: User = Depends(require_permission("system.backup"))):
    if not db_backup.is_supported():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El respaldo solo funciona con la base de producción (MySQL).")
    try:
        item = await asyncio.to_thread(db_backup.run_backup, True)
    except Exception as exc:
        logger.exception("[Backup] Falló el respaldo manual.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"No se pudo hacer el respaldo: {exc}")
    _audit(db, current_user, "backup.create", item["name"])
    return item


@router.get("/backups/{name}")
def download_backup(name: str, db: Session = Depends(get_db), current_user: User = Depends(require_permission("system.backup"))):
    path = db_backup.backup_path(name)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respaldo no encontrado.")
    _audit(db, current_user, "backup.download", name)
    return FileResponse(str(path), media_type="application/gzip", filename=name,
                        headers={"Cache-Control": "no-store"})


# ==========================================================================
# Actividad (auditoría): quién hizo qué, cuándo y en qué sucursal
# ==========================================================================
import json  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402
from typing import Optional  # noqa: E402

from fastapi import Query  # noqa: E402
from sqlalchemy.orm import joinedload  # noqa: E402

from models.audit import AuditEvent  # noqa: E402
from routers.inventory import _visible_branch_filter  # noqa: E402
from security.permissions import has_permission  # noqa: E402

# Lo que es administración del sistema (usuarios, dispositivos, respaldos) solo lo ve un admin.
ADMIN_ONLY_PREFIXES = ("user.", "device.", "backup.", "integration.", "permission.")
PANAMA_OFFSET = timedelta(hours=5)


@router.get("/audit")
def list_audit(
    branch_id: Optional[int] = Query(None),
    actor_user_id: Optional[int] = Query(None),
    group: Optional[str] = Query(None, max_length=30, description="Prefijo de la acción: task, waste, count, shipment…"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports.view")),
):
    """
    La actividad registrada, lo más nuevo primero. Admin y gerente (supervisor sin sucursal) ven
    todas las sucursales; un encargado, solo la suya. Lo de administración del sistema (usuarios,
    dispositivos, respaldos) y los eventos sin sucursal solo los ve un admin.
    """
    es_admin = has_permission(current_user, "users.manage")
    efectiva = _visible_branch_filter(current_user, branch_id)
    q = db.query(AuditEvent).options(joinedload(AuditEvent.actor_user), joinedload(AuditEvent.branch))
    if efectiva is not None:
        q = q.filter(AuditEvent.branch_id == efectiva)
    elif not es_admin:
        q = q.filter(AuditEvent.branch_id.isnot(None))
    if not es_admin:
        for pref in ADMIN_ONLY_PREFIXES:
            q = q.filter(~AuditEvent.action.like(f"{pref}%"))
    if actor_user_id is not None:
        q = q.filter(AuditEvent.actor_user_id == actor_user_id)
    if group:
        g = group.strip().rstrip(".")
        q = q.filter(AuditEvent.action.like(f"{g}.%"))
    if date_from:
        q = q.filter(AuditEvent.created_at >= datetime.combine(date_from, datetime.min.time()) + PANAMA_OFFSET)
    if date_to:
        q = q.filter(AuditEvent.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()) + PANAMA_OFFSET)

    filas = q.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset(offset).limit(limit + 1).all()
    hay_mas = len(filas) > limit
    eventos = []
    for e in filas[:limit]:
        try:
            meta = json.loads(e.metadata_json) if e.metadata_json else {}
        except (ValueError, TypeError):
            meta = {}
        eventos.append({
            "id": e.id, "created_at": e.created_at, "action": e.action,
            "entity_type": e.entity_type, "entity_id": e.entity_id,
            "actor": {"id": e.actor_user.id, "name": e.actor_user.name} if e.actor_user else None,
            "branch": {"id": e.branch.id, "name": e.branch.name} if e.branch else None,
            "metadata": meta if isinstance(meta, dict) else {},
        })
    return {"events": eventos, "has_more": hay_mas, "offset": offset, "limit": limit, "is_admin": es_admin}
