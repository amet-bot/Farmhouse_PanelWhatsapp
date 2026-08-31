import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import settings, BASE_DIR
from database import get_db
from models.message import Message
from models.user import User
from security.auth import get_current_authorized_user

logger = logging.getLogger("farmhouse.media")

router = APIRouter(prefix="/media", tags=["Archivos Multimedia"])

MEDIA_DIR = (BASE_DIR / "media").resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/{file_name:path}")
def get_authenticated_media(
    file_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Endpoint autenticado y seguro para descarga y visualización de archivos multimedia (Punto 6).
    Verifica que el usuario tenga sesión activa y permisos sobre la sucursal de la conversación.
    """
    clean_name = Path(file_name).name
    target_path = (MEDIA_DIR / clean_name).resolve()
    
    if not str(target_path).startswith(str(MEDIA_DIR)):
        logger.warning(f"Intento de path traversal detectado por usuario {current_user.id}: '{file_name}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ruta de archivo no permitida."
        )

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El archivo multimedia no fue encontrado en el servidor."
        )

    # Validar autorización de acceso por sucursal mediante el mensaje
    msg = db.query(Message).filter(
        Message.media_url.like(f"%{clean_name}%")
    ).first()

    if msg and msg.conversation:
        conv = msg.conversation
        if current_user.role == "agent":
            if conv.branch_id != current_user.branch_id:
                logger.warning(f"Acceso denegado a media: Agente {current_user.id} intentó acceder a archivo de conv {conv.id} (sucursal {conv.branch_id}).")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para ver archivos multimedia de otra sucursal."
                )
        elif current_user.role == "supervisor" and current_user.branch_id:
            if conv.branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para ver archivos multimedia de otra sucursal."
                )

    mime_type = (msg.media_mime_type if msg else None) or "application/octet-stream"

    return FileResponse(
        path=str(target_path),
        media_type=mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"inline; filename=\"{clean_name}\"",
            "Cache-Control": "private, max-age=3600"
        }
    )
