import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from config import settings, BASE_DIR
from database import get_db
from models.internal_chat import InternalMessage, InternalParticipant
from models.message import Message
from models.user import User

logger = logging.getLogger("farmhouse.media")

router = APIRouter(prefix="/media", tags=["Archivos Multimedia"])

MEDIA_DIR = (BASE_DIR / "media").resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR = (MEDIA_DIR / "incoming").resolve()
INCOMING_DIR.mkdir(parents=True, exist_ok=True)
INTERNAL_DIR = (MEDIA_DIR / "internal").resolve()
INTERNAL_DIR.mkdir(parents=True, exist_ok=True)

# Tipos que el navegador puede mostrar sin ejecutar nada. Cualquier otro (text/html,
# image/svg+xml, application/javascript...) se entrega como descarga: un documento que manda un
# cliente por WhatsApp con mime text/html, servido "inline" desde el mismo origen del panel,
# correría su JavaScript con la sesión del agente que lo abre (XSS almacenado).
_INLINE_SAFE_PREFIXES = ("image/", "audio/", "video/")
_INLINE_SAFE_TYPES = {"application/pdf"}
_INLINE_UNSAFE_TYPES = {"image/svg+xml"}


def _media_response(path: Path, mime_type: Optional[str], filename: str) -> FileResponse:
    mime = (mime_type or "application/octet-stream").split(";")[0].strip().lower()
    inline = mime not in _INLINE_UNSAFE_TYPES and (
        mime in _INLINE_SAFE_TYPES or mime.startswith(_INLINE_SAFE_PREFIXES)
    )
    return FileResponse(
        path=str(path),
        media_type=mime if inline else "application/octet-stream",
        # filename= deja que Starlette arme Content-Disposition con filename*=UTF-8''...: un
        # nombre con comillas, raya larga o emoji ya no rompe el encabezado (antes daba 500).
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


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

    # 2a. Adjuntos de Comunicación Interna: la sucursal no decide nada acá (un hilo directo
    # cruza sucursales a propósito). Lo que decide es si quien pide participa del hilo, el
    # mismo criterio que para leer los mensajes. Sin esta rama el archivo quedaría legible
    # para cualquiera con sesión, porque abajo solo se mira la conversación de WhatsApp a la
    # que pertenece el archivo — y un adjunto interno no pertenece a ninguna.
    if target_path.parent == INTERNAL_DIR:
        internal_msg = db.query(InternalMessage).filter(
            InternalMessage.media_url.like(f"%{clean_name}%")
        ).first()
        if not internal_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El archivo multimedia no fue encontrado en el servidor."
            )
        participa = db.query(InternalParticipant).filter(
            InternalParticipant.thread_id == internal_msg.thread_id,
            InternalParticipant.user_id == current_user.id,
        ).first()
        if not participa:
            logger.warning(
                f"Acceso denegado a adjunto interno: usuario {current_user.id} pidió un archivo "
                f"del hilo {internal_msg.thread_id}, del que no participa."
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés permiso para ver este archivo."
            )
        return _media_response(target_path, internal_msg.media_mime_type, internal_msg.media_name or clean_name)

    # 2b. Validar autorización de acceso por sucursal mediante el mensaje
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

    return _media_response(target_path, msg.media_mime_type if msg else None, clean_name)
