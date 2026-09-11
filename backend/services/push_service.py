import json
import logging
from typing import Optional
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    class WebPushException(Exception):
        response = None

from config import settings
from models.user import User
from models.push_subscription import PushSubscription

logger = logging.getLogger("farmhouse.push")

def is_push_configured() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)

def _send_to_subscription(db: Session, sub: PushSubscription, payload: dict) -> None:
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
            },
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_CLAIM_SUB},
            ttl=60
        )
    except WebPushException as e:
        status_code = e.response.status_code if e.response is not None else None
        if status_code in (404, 410):
            # El navegador revocó o expiró la suscripción: eliminarla para no reintentar en el futuro.
            db.query(PushSubscription).filter(PushSubscription.id == sub.id).delete()
            db.commit()
            logger.info(f"[Push] Suscripción expirada/revocada eliminada (user_id={sub.user_id}, sub_id={sub.id}).")
        else:
            logger.warning(f"[Push] Error enviando notificación a user_id={sub.user_id}: {e}")
    except Exception as e:
        logger.error(f"[Push] Error inesperado enviando a user_id={sub.user_id}: {e}", exc_info=True)

def notify_branch_new_message(db: Session, branch_id: Optional[int], title: str, body: str, conversation_id: int) -> None:
    """
    Envía notificaciones push a quien tiene derecho a ver la conversación: admins,
    supervisores globales y los usuarios de esa misma sucursal. Usa exactamente el mismo
    criterio que la difusión en tiempo real por WebSocket
    (ver services/notification_audience.py y ConnectionManager.broadcast_to_branch).
    branch_id puede ser None (conversación aún sin sucursal asignada): en ese caso solo
    califican los que ven global (admin y supervisor sin sucursal), nunca agentes ni
    supervisores de sucursal.
    No hace nada si el servidor no tiene VAPID configurado (Web Push deshabilitado).
    """
    if not is_push_configured():
        return

    # Traducción a SQL de services/notification_audience.can_receive_branch_event.
    # Antes la condición era `User.role.in_(["admin", "supervisor"])`, que le mandaba las
    # notificaciones de TODAS las sucursales a un supervisor asignado a una sola.
    audience_conditions = [
        User.role == "admin",
        and_(User.role == "supervisor", User.branch_id.is_(None)),
    ]
    if branch_id is not None:
        # Agentes y supervisores de esa misma sucursal.
        audience_conditions.append(User.branch_id == branch_id)
    audience_condition = or_(*audience_conditions)

    target_users = db.query(User).filter(
        User.active == True,
        audience_condition
    ).all()
    if not target_users:
        return

    user_ids = [u.id for u in target_users]
    subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
    if not subs:
        return

    payload = {
        "title": title,
        "body": (body or "Nuevo mensaje")[:100],
        "url": f"/?conversation_id={conversation_id}",
        "conversation_id": conversation_id
    }
    for sub in subs:
        _send_to_subscription(db, sub, payload)
