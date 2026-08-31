import logging
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session
from datetime import timedelta, datetime
from sqlalchemy import func

from database import get_db
from models.user import User
from models.device import Device
from schemas.auth import LoginRequest, TokenResponse
from schemas.user import UserResponse
from security.auth import verify_password, create_access_token, get_current_user
from config import settings

logger = logging.getLogger("farmhouse.auth")

router = APIRouter(prefix="/auth", tags=["Autenticación"])

# -----------------------------------------------------------------------------
# Rate Limiting en memoria para intentos de login (Puntos 5 y 13)
# Bloquea temporalmente tras 5 intentos fallidos en una ventana de 5 minutos (300s).
# -----------------------------------------------------------------------------
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300
MAX_TRACKED_KEYS = 5000

# Estructura: key -> list of timestamps
_failed_login_attempts = defaultdict(list)
_ws_single_use_tickets: dict[str, dict] = {}

def _get_client_ip(request: Request) -> str:
    """Extrae la IP real del cliente considerando cabeceras de proxy de forma segura."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Tomar la primera IP de la cadena de proxies
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    return request.client.host if request.client else "127.0.0.1"

def _get_rate_limit_key(request: Request, username: str) -> str:
    client_ip = _get_client_ip(request)
    return f"{client_ip}:{username.strip().lower()}"

def _check_rate_limit(key: str):
    now = time.time()
    # Limpieza de memoria si la colección crece demasiado
    if len(_failed_login_attempts) > MAX_TRACKED_KEYS:
        expired_keys = [k for k, timestamps in _failed_login_attempts.items() if not timestamps or (now - timestamps[-1] > LOCKOUT_DURATION_SECONDS)]
        for k in expired_keys:
            _failed_login_attempts.pop(k, None)

    # Limpiar timestamps viejos fuera de la ventana
    _failed_login_attempts[key] = [t for t in _failed_login_attempts[key] if now - t < LOCKOUT_DURATION_SECONDS]
    if len(_failed_login_attempts[key]) >= MAX_FAILED_ATTEMPTS:
        oldest = _failed_login_attempts[key][0]
        remaining = int(LOCKOUT_DURATION_SECONDS - (now - oldest))
        logger.warning(f"Rate limit excedido para '{key}'. Bloqueado por {remaining}s.")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos de inicio de sesión. Por favor espera {remaining} segundos."
        )

def _record_failed_attempt(key: str):
    _failed_login_attempts[key].append(time.time())

def _clear_failed_attempts(key: str):
    _failed_login_attempts.pop(key, None)

@router.post("/login", response_model=TokenResponse)
def login(login_data: LoginRequest, response: Response, request: Request, db: Session = Depends(get_db)):
    username_clean = login_data.username.strip().lower()
    rate_key = _get_rate_limit_key(request, username_clean)

    # 1. Verificar Rate Limit
    _check_rate_limit(rate_key)

    client_ip = _get_client_ip(request)
    logger.info(f"Intento de login para usuario: '{username_clean}' desde IP {client_ip}")

    user = db.query(User).filter(func.lower(User.username) == username_clean).first()
    if not user:
        _record_failed_attempt(rate_key)
        logger.warning(f"Login fallido: Usuario '{username_clean}' no encontrado.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nombre de usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(login_data.password, user.password_hash):
        _record_failed_attempt(rate_key)
        logger.warning(f"Login fallido: Contraseña incorrecta para usuario ID {user.id} ('{username_clean}').")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nombre de usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.active:
        logger.warning(f"Login fallido: Usuario ID {user.id} ('{username_clean}') está inactivo.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta de usuario se encuentra inactiva o desactivada."
        )

    # Login exitoso: limpiar historial de intentos fallidos
    _clear_failed_attempts(rate_key)

    # Actualizar last_seen si se envía X-Device-ID
    device_id_header = (request.headers.get("X-Device-ID") or request.headers.get("x-device-id") or "").strip()
    if device_id_header:
        dev = db.query(Device).filter(Device.device_id == device_id_header).first()
        if dev and dev.status == "active":
            dev.last_seen = datetime.utcnow()
            db.commit()
            logger.info(f"Dispositivo verificado en login: {dev.name} [{dev.device_id}]")

    logger.info(f"Login exitoso: '{user.name}' (ID: {user.id}, Rol: '{user.role}', Sucursal: {user.branch.name if user.branch else 'Global'})")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=user.id,
        expires_delta=access_token_expires
    )

    # Configurar cookie de autenticación segura HttpOnly
    is_secure_cookie = settings.ENVIRONMENT.lower() != "development"
    max_age_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=max_age_seconds,
        expires=max_age_seconds,
        samesite="lax",
        secure=is_secure_cookie,
        path="/"
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(user)
    )

@router.post("/logout")
def logout(response: Response):
    is_secure_cookie = settings.ENVIRONMENT.lower() != "development"
    response.delete_cookie(
        key="access_token",
        path="/",
        samesite="lax",
        httponly=True,
        secure=is_secure_cookie
    )
    logger.info("Sesión cerrada exitosamente vía logout.")
    return {"message": "Sesión cerrada exitosamente."}

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

def generate_ws_ticket(user_id: int) -> str:
    """Genera un ticket efímero de uso único para la conexión WebSocket (Punto 14)."""
    import uuid
    ticket = f"wst_{uuid.uuid4().hex}"
    now = time.time()
    # Limpiar tickets viejos (> 60 segundos)
    expired = [t for t, data in _ws_single_use_tickets.items() if now - data["created_at"] > 60]
    for t in expired:
        _ws_single_use_tickets.pop(t, None)
    
    _ws_single_use_tickets[ticket] = {
        "user_id": user_id,
        "created_at": now
    }
    return ticket

def consume_ws_ticket(ticket: str) -> int | None:
    """Consume y valida un ticket WebSocket de un solo uso."""
    now = time.time()
    data = _ws_single_use_tickets.pop(ticket, None)
    if not data:
        return None
    if now - data["created_at"] > 60:
        return None
    return data["user_id"]

@router.get("/ws-token")
@router.post("/ws-token")
def get_ws_token(current_user: User = Depends(get_current_user)):
    """
    Entrega un ticket efímero o token JWT de corta vida para autenticar el handshake de WebSocket (Punto 14).
    """
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=current_user.id,
        expires_delta=access_token_expires
    )
    ticket = generate_ws_ticket(current_user.id)
    return {
        "access_token": access_token,
        "ws_ticket": ticket
    }

