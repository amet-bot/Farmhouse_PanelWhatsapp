import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from config import settings, BASE_DIR
from database import get_db
from models.message import Message
from models.user import User

logger = logging.getLogger("farmhouse.media")

router = APIRouter(prefix="/media", tags=["Archivos Multimedia"])

MEDIA_DIR = (BASE_DIR / "media").resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR = (MEDIA_DIR / "incoming").resolve()
INCOMING_DIR.mkdir(parents=True, exist_ok=True)

def authenticate_media_user(
    request: Request,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
) -> User:
    """
    Autentica la solicitud de medios desde cookie HttpOnly, encabezado Authorization o parámetro ?token=.
    Permite a etiquetas <img> y <audio>/<video> cargar medios protegidos de forma segura.
    """
    raw_token = token or request.cookies.get("access_token")
    if not raw_token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_token = auth_hdr[7:].strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticación para acceder a los archivos multimedia."
        )

    try:
        payload = jwt.decode(raw_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Token de medios inválido.")
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Token de medios expirado o inválido.")

    user = db.query(User).filter(User.id == user_id, User.active == True).first()
    if not user:
        raise HTTPException(status_code=403, detail="Usuario inactivo o inexistente.")
    return user

@router.get("/{file_name:path}")
def get_authenticated_media(
    file_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(authenticate_media_user)
):
    """
    Endpoint autenticado y seguro para descarga y visualización de archivos multimedia (Punto 6).
    Verifica que el usuario tenga sesión activa y permisos sobre la sucursal de la conversación.
    """
    clean_name = Path(file_name).name
    
    # 1. Buscar en subcarpeta incoming o raíz de media
    target_path = (MEDIA_DIR / file_name).resolve()
    if not target_path.exists() or not target_path.is_file():
        target_path = (INCOMING_DIR / clean_name).resolve()
    if not target_path.exists() or not target_path.is_file():
        target_path = (MEDIA_DIR / clean_name).resolve()

    # Seguridad contra Path Traversal
    try:
        target_path.relative_to(MEDIA_DIR)
    except ValueError:
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

    # 2. Validar autorización de acceso por sucursal mediante el mensaje
    msg = db.query(Message).filter(
        Message.media_url.like(f"%{clean_name}%")
    ).first()

    if msg and msg.conversation:
        conv = msg.conversation
        if current_user.role == "agent":
            if conv.branch_id and conv.branch_id != current_user.branch_id:
                logger.warning(f"Acceso denegado a media: Agente {current_user.id} intentó acceder a archivo de conv {conv.id} (sucursal {conv.branch_id}).")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tienes permiso para ver archivos multimedia de otra sucursal."
                )
        elif current_user.role == "supervisor" and current_user.branch_id:
            if conv.branch_id and conv.branch_id != current_user.branch_id:
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
