import json
import logging
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

def notify_branch_new_message(db: Session, branch_id: int, title: str, body: str, conversation_id: int) -> None:
    """
    Envía notificaciones push a los agentes/encargados de la sucursal indicada y a todos
    los supervisores/administradores activos (mismo criterio de audiencia que la difusión
    en tiempo real por WebSocket, ver ConnectionManager.broadcast_to_branch).
    No hace nada si el servidor no tiene VAPID configurado (Web Push deshabilitado).
    """
    if not is_push_configured() or not branch_id:
        return

    target_users = db.query(User).filter(
        User.active == True,
        (User.branch_id == branch_id) | (User.role.in_(["admin", "supervisor"]))
    ).all()
    if not target_users:
        return

    user_ids = [u.id for u in target_users]
    subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
    if not subs:
        return

    payload = {
        "title": title,
        "body": (body or "Nuevo mensaje")[:180],
        "url": f"/?conversation_id={conversation_id}",
        "conversation_id": conversation_id
    }
    for sub in subs:
        _send_to_subscription(db, sub, payload)
