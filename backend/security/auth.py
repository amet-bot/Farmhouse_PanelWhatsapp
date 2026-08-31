import logging
from datetime import datetime, timedelta
from typing import Optional, Union, Any
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.user import User
from models.device import Device
from services.device_access import check_device_authorized

logger = logging.getLogger("farmhouse.security")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login", auto_error=False)

import uuid
from datetime import datetime, timezone, timedelta

def validate_password_strength(password: str) -> bool:
    """Valida que la contraseña cumpla los estándares mínimos de longitud (10+ caracteres)."""
    if not password or len(password.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña debe contener al menos 10 caracteres."
        )
    return True

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Compara la contraseña en texto plano contra el hash bcrypt"""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:72],
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """Genera hash bcrypt seguro para almacenamiento en base de datos"""
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "exp": expire,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "sub": str(subject)
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def get_token_from_request(request: Request, header_token: Optional[str] = None) -> Optional[str]:
    # 1. Preferencia por Cookie HttpOnly de sesión
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    # 2. Header Authorization Bearer
    if header_token:
        return header_token
    auth_hdr = request.headers.get("Authorization")
    if auth_hdr and auth_hdr.startswith("Bearer "):
        return auth_hdr[7:].strip()
    return None

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token_from_header: Optional[str] = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tu sesión expiró. Inicia sesión nuevamente.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = get_token_from_request(request, token_from_header)
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id, User.active == True).first()
    if user is None:
        raise credentials_exception
    return user

def validate_device_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Optional[Device]:
    """
    CONTROL DE ACCESO DUAL: Usuario Autorizado + Dispositivo Autorizado
    Utiliza el servicio centralizado check_device_authorized.
    """
    device_id_header = (request.headers.get("X-Device-ID") or request.headers.get("x-device-id") or "").strip()

    # Administradores: Acceso global con tracking si se envía dispositivo
    if current_user.role == "admin":
        if device_id_header:
            dev = db.query(Device).filter(Device.device_id == device_id_header).first()
            if dev and dev.status == "active":
                dev.last_seen = datetime.utcnow()
                db.commit()
                return dev
        return None

    # Agentes y Supervisores: Requieren dispositivo físico/terminal autorizado
    return check_device_authorized(db, device_id_header, current_user)

def get_current_authorized_user(
    current_user: User = Depends(get_current_user),
    device: Optional[Device] = Depends(validate_device_access)
) -> User:
    return current_user

def require_role(allowed_roles: list[str]):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso denegado. Se requiere uno de los siguientes roles: {', '.join(allowed_roles)}"
            )
        return current_user
    return role_checker
