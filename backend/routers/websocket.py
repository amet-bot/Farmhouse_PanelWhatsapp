import logging
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt
from config import settings
from database import SessionLocal
from models.user import User
from models.device import Device
from services.websocket_manager import ws_manager
from services.device_access import check_device_authorized

logger = logging.getLogger("farmhouse.websocket")

router = APIRouter(tags=["WebSockets"])

from typing import Optional
from routers.auth import consume_ws_ticket

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    ticket: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None)
):
    user_id = None
    auth_val = ticket or token

    if not auth_val:
        logger.warning("Conexión WebSocket rechazada: No se proporcionó token ni ticket de autenticación.")
        await websocket.close(code=1008)
        return

    # 1. Intentar validar como ticket de un solo uso
    if auth_val.startswith("wst_"):
        user_id = consume_ws_ticket(auth_val)
        if not user_id:
            logger.warning("Conexión WebSocket rechazada: Ticket WebSocket inválido, ya consumido o expirado.")
            await websocket.close(code=1008)
            return

    # 2. Si no es ticket, validar como Token JWT
    if not user_id:
        try:
            payload = jwt.decode(auth_val, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id_str = payload.get("sub")
            if not user_id_str:
                await websocket.close(code=1008)
                return
            user_id = int(user_id_str)
        except (JWTError, ValueError):
            logger.warning("Conexión WebSocket rechazada: Token JWT inválido o expirado.")
            await websocket.close(code=1008)
            return

    # 2. Validar Usuario y Dispositivo Autorizado
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.active == True).first()
        if not user:
            logger.warning(f"Conexión WebSocket rechazada: Usuario ID {user_id} inactivo o inexistente.")
            await websocket.close(code=1008)
            return

        # Validar dispositivo mediante servicio centralizado check_device_authorized
        if user.role != "admin":
            try:
                check_device_authorized(db, device_id, user)
            except Exception as e:
                logger.warning(f"Conexión WebSocket rechazada por validación de dispositivo: {e.detail if hasattr(e, 'detail') else e}")
                await websocket.close(code=1008)
                return
        elif device_id:
            dev = db.query(Device).filter(Device.device_id == device_id, Device.status == "active").first()
            if dev:
                dev.last_seen = datetime.utcnow()
                db.commit()

        branch_id = user.branch_id
        role = user.role
        user_name = user.name
    finally:
        db.close()

    # 3. Conectar a salas segmentadas
    await ws_manager.connect(websocket, user_id=user_id, branch_id=branch_id, role=role)
    logger.info(f"Conexión WebSocket establecida: Usuario '{user_name}' (ID: {user_id}, Rol: {role}, Sucursal: {branch_id}, Dispositivo: {device_id or 'Global Admin'})")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id=user_id, branch_id=branch_id, role=role)
        logger.info(f"Conexión WebSocket cerrada: Usuario ID {user_id} ({user_name}).")
