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
