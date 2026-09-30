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
from models.native_push import NativePushToken
from services import fcm_service

logger = logging.getLogger("farmhouse.push")

def is_push_configured() -> bool:
    """Web Push del navegador (VAPID)."""
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def any_channel_configured() -> bool:
    """Si algún aviso puede salir: por el navegador o por la app nativa (Firebase)."""
    return is_push_configured() or fcm_service.is_configured()


def _send_native(db: Session, user_ids: list, payload: dict) -> int:
    """
    Manda el aviso a los celulares con la app (FCM) de esos usuarios. Un token que Firebase da por
    muerto (se desinstaló la app) se borra para no reintentar. Devuelve a cuántos se mandó.
    """
    if not user_ids or not fcm_service.is_configured():
        return 0
    enviados = 0
    for t in db.query(NativePushToken).filter(NativePushToken.user_id.in_(user_ids)).all():
        try:
            fcm_service.send(t.token, payload.get("title") or "Farmhouse Link", payload.get("body") or "",
                             url=payload.get("url"), tag=payload.get("tag"))
            enviados += 1
        except fcm_service.TokenInvalido:
            db.query(NativePushToken).filter(NativePushToken.id == t.id).delete()
            db.commit()
            logger.info(f"[Push] Token nativo vencido eliminado (user_id={t.user_id}).")
        except Exception as e:
            logger.warning(f"[Push] Error mandando aviso nativo a user_id={t.user_id}: {e}")
    return enviados


def _deliver(db: Session, user_ids: list, payload: dict) -> int:
    """El mismo aviso por los dos caminos: navegador (Web Push) y app nativa (FCM)."""
    if not user_ids:
        return 0
    enviados = 0
    if is_push_configured():
        for sub in db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all():
            _send_to_subscription(db, sub, payload)
            enviados += 1
    return enviados + _send_native(db, user_ids, payload)

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
        if status_code in (401, 403, 404, 410):
            # 404/410: el navegador revocó o expiró la suscripción. 401/403: la suscripción se
            # creó con otras claves VAPID (p. ej. se regeneraron al reiniciar sin variables de
            # entorno) y no va a volver a funcionar. En ambos casos se elimina para no reintentar
            # en cada mensaje; el panel se vuelve a suscribir con la clave vigente al abrirse.
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
    Sale por el navegador (VAPID) y por la app nativa (Firebase), lo que esté configurado.
    """
    if not any_channel_configured():
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

    payload = {
        "title": title,
        "body": (body or "Nuevo mensaje")[:100],
        # "/app" (no "/"): "/" ahora es el Panel General de sistemas, no el Centro WhatsApp — un
        # clic en la notificación debe abrir la conversación directo, sin pasar por el hub.
        "url": f"/app?conversation_id={conversation_id}",
        "conversation_id": conversation_id,
        "tag": f"fh-conv-{conversation_id}",
    }
    _deliver(db, user_ids, payload)


def notify_internal_message(
    db: Session,
    thread_id: int,
    thread_title: str,
    sender_name: str,
    body: str,
    recipient_user_ids: list,
) -> None:
    """
    Avisa por push de un mensaje de Comunicación Interna.

    A diferencia de notify_branch_new_message, acá la audiencia NO se deduce de la sucursal:
    un directo cruza sucursales a propósito, así que el destinatario es quien participa del
    hilo y nadie más. La lista llega ya resuelta desde el router, que es quien conoce el hilo.

    El título es de quién escribe y el cuerpo es el mensaje: alcanza para decidir si vale la
    pena abrir el panel sin tener que abrirlo. Un canal de equipo antepone el nombre del canal
    porque "Juan" solo no dice si te escribió a vos o al grupo.
    """
    if not any_channel_configured() or not recipient_user_ids:
        return

    payload = {
        "title": sender_name if thread_title == sender_name else f"{sender_name} · {thread_title}",
        "body": (body or "Te mandaron un archivo")[:100],
        "url": f"/interno?thread={thread_id}",
        # Una notificación por hilo, que se reemplaza: diez mensajes seguidos del mismo canal
        # no pueden dejar diez avisos apilados en la pantalla de bloqueo.
        "tag": f"fh-internal-{thread_id}",
        "internal_thread_id": thread_id,
    }
    _deliver(db, list(recipient_user_ids), payload)


def notify_users(db: Session, user_ids: list, title: str, body: str, url: str, tag: Optional[str] = None) -> int:
    """Aviso directo a personas concretas (ej. a quien le asignaron una tarea o incidencia)."""
    if not user_ids or not any_channel_configured():
        return 0
    activos = [u.id for u in db.query(User.id).filter(User.id.in_(user_ids), User.active == True).all()]  # noqa: E712
    payload = {"title": title, "body": (body or "")[:140], "url": url}
    if tag:
        payload["tag"] = tag
    return _deliver(db, activos, payload)


def notify_branch_staff(
    db: Session,
    branch_id: int,
    title: str,
    body: str,
    url: str,
    tag: Optional[str] = None,
    managers_only: bool = False,
) -> int:
    """
    Avisa por push a la gente de una sucursal: sus usuarios, los supervisores globales y los
    admins (misma audiencia que notify_branch_new_message). Con `managers_only` quedan fuera los
    agentes: una diferencia en un cargamento la tiene que resolver un supervisor, no la cocina.
    Devuelve a cuántos navegadores y celulares se mandó (0 si no hay nada configurado).
    """
    if not any_channel_configured():
        return 0
    condiciones = [
        User.role == "admin",
        and_(User.role == "supervisor", User.branch_id.is_(None)),
    ]
    if managers_only:
        condiciones.append(and_(User.role == "supervisor", User.branch_id == branch_id))
    else:
        condiciones.append(User.branch_id == branch_id)
    user_ids = [u.id for u in db.query(User.id).filter(User.active == True, or_(*condiciones)).all()]  # noqa: E712
    payload = {"title": title, "body": (body or "")[:140], "url": url}
    if tag:
        payload["tag"] = tag
    return _deliver(db, user_ids, payload)
