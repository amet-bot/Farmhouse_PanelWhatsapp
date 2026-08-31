import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from config import settings
from models.user import User
from models.push_subscription import PushSubscription
from schemas.push import PushSubscriptionCreate, PushUnsubscribe
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
