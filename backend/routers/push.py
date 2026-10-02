import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from config import settings
from models.user import User
from models.native_push import NativePushToken
from models.push_subscription import PushSubscription
from schemas.push import NativeTokenIn, PushSubscriptionCreate, PushUnsubscribe
from services import fcm_service, push_service
from security.auth import get_current_user

logger = logging.getLogger("farmhouse.push")

router = APIRouter(prefix="/push", tags=["Notificaciones Push"])

@router.get("/vapid-public-key")
def get_vapid_public_key():
    """Clave pública VAPID para que el navegador cree la suscripción (PushManager.subscribe)."""
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Las notificaciones push no están configuradas en el servidor."
        )
    return {"public_key": settings.VAPID_PUBLIC_KEY}

@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    sub_in: PushSubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == sub_in.endpoint).first()
    if existing:
        # Un mismo dispositivo/navegador puede reasignarse a otro usuario (ej. terminal compartida).
        existing.user_id = current_user.id
        existing.p256dh = sub_in.keys.p256dh
        existing.auth = sub_in.keys.auth
        existing.user_agent = sub_in.user_agent
        db.commit()
        return {"status": "updated"}

    sub = PushSubscription(
        user_id=current_user.id,
        endpoint=sub_in.endpoint,
        p256dh=sub_in.keys.p256dh,
        auth=sub_in.keys.auth,
        user_agent=sub_in.user_agent
    )
    db.add(sub)
    db.commit()
    logger.info(f"[Push] Nueva suscripción registrada para @{current_user.username} (ID {current_user.id}).")
    return {"status": "subscribed"}

@router.post("/unsubscribe")
def unsubscribe(
    sub_in: PushUnsubscribe,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == sub_in.endpoint,
        PushSubscription.user_id == current_user.id
    ).delete()
    db.commit()
    return {"status": "unsubscribed"}


# ==========================================================================
# App de Android: notificaciones nativas por Firebase
# ==========================================================================
@router.get("/native/status")
def native_status(current_user: User = Depends(get_current_user)):
    """Si el servidor puede mandar avisos a la app (hay cuenta de Firebase configurada)."""
    return {"enabled": fcm_service.is_configured()}


@router.post("/native/register", status_code=status.HTTP_201_CREATED)
def native_register(
    data: NativeTokenIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Guarda el token del celular para el usuario que tiene la sesión abierta. Si el mismo celular
    ya estaba registrado a nombre de otro (un teléfono compartido en la sucursal), pasa a este.
    """
    ahora = datetime.now(timezone.utc)
    existente = db.query(NativePushToken).filter(NativePushToken.token == data.token).first()
    if existente:
        existente.user_id = current_user.id
        existente.platform = data.platform
        existente.last_seen_at = ahora
        db.commit()
        return {"status": "updated"}
    db.add(NativePushToken(user_id=current_user.id, token=data.token, platform=data.platform,
                           created_at=ahora, last_seen_at=ahora))
    db.commit()
    logger.info(f"[Push] App nativa registrada para @{current_user.username} (ID {current_user.id}).")
    return {"status": "registered"}


@router.post("/native/unregister")
def native_unregister(
    data: NativeTokenIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Al cerrar sesión en la app: ese celular deja de recibir los avisos de este usuario."""
    db.query(NativePushToken).filter(
        NativePushToken.token == data.token, NativePushToken.user_id == current_user.id,
    ).delete()
    db.commit()
    return {"status": "unregistered"}


@router.post("/native/test")
def native_test(
    channel: str = Query("avisos", pattern="^(avisos|tareas|recordatorios)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manda una notificación de prueba a los celulares de quien la pide: para comprobar que
    llegan. `channel` deja probar el de tareas (sonido distinto) sin tener que crear una tarea de
    verdad — útil porque crear/recordar una tarea nunca se auto-notifica a quien la dispara."""
    if not fcm_service.is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Las notificaciones de la app todavía no están configuradas en el servidor (falta Firebase).")
    canal = {"tareas": fcm_service.CANAL_TAREAS, "recordatorios": fcm_service.CANAL_RECORDATORIOS}.get(channel, fcm_service.CANAL_AVISOS)
    cuerpo = {
        "tareas": "Si suena distinto a un mensaje, el canal de tareas ya funciona.",
        "recordatorios": "Si suena como alarma, el canal de recordatorios ya funciona.",
    }.get(channel, "¡Listo! Las notificaciones de la app te llegan a este celular.")
    enviados = push_service._send_native(db, [current_user.id], {
        "title": "Farmhouse Link", "body": cuerpo,
        "url": "/hub", "tag": "fh-test", "channel": canal,
    })
    if not enviados:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Este usuario no tiene la app registrada en ningún celular.")
    return {"sent": enviados, "channel": canal}
