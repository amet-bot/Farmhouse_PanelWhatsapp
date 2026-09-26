"""
Seguimiento automático del bot cuando el cliente deja de responder a mitad del flujo.

Decisión del negocio: si pasan 5+ minutos desde el último mensaje del bot sin que el cliente
responda, el bot manda UN único mensaje de reenganche ("¿sigues ahí?"). No insiste una segunda
vez para esa misma pausa — si el cliente responde y más adelante vuelve a quedarse callado, sí
puede recibir otro seguimiento (ver `bot_followup_sent_at` en models/conversation.py).

El proyecto no tiene scheduler/cron (ver el comentario en Conversation.needs_reminder: ese
recordatorio se calcula "al vuelo" cuando el panel pide la conversación, porque es solo un
badge). Este caso es distinto: mandar un mensaje es una ACCIÓN real que tiene que dispararse en
un momento preciso, no algo que se pueda calcular de forma perezosa — por eso este módulo agrega
el único loop en segundo plano que tiene el proyecto, arrancado una vez en el lifespan de
main.py (nunca durante los tests: ver la guarda por PYTEST_CURRENT_TEST ahí mismo, porque si no
cada test terminaría abriendo una conexión real a la base de datos de verdad en segundo plano).
"""
import asyncio
import logging
from datetime import datetime, timedelta

from database import SessionLocal
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import BOT_FOLLOWUP_ENABLED, BOT_FOLLOWUP_MESSAGE
from services.flow_content import get_node_text
from services.whatsapp_service import get_whatsapp_service

logger = logging.getLogger("farmhouse.bot_followup")

FOLLOWUP_THRESHOLD_MINUTES = 5
# Pasada esta antigüedad del último mensaje del bot ya no se manda el seguimiento.
FOLLOWUP_MAX_AGE_MINUTES = 60
# Revisa cada minuto: con un umbral de 5 minutos, un margen de 1 minuto es suficiente
# resolución sin generar carga innecesaria en la base de datos.
SWEEP_INTERVAL_SECONDS = 60


async def run_followup_sweep_loop() -> None:
    """Corre indefinidamente hasta que la tarea se cancele en el shutdown de la app. Una
    excepción en una pasada no debe tumbar el loop entero — se registra y se sigue."""
    while True:
        try:
            await _sweep_once()
        except Exception:
            logger.exception("[BotFollowup] Error en una pasada del seguimiento automático.")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


async def _sweep_once() -> None:
    if not BOT_FOLLOWUP_ENABLED:
        return
    db = SessionLocal()
    try:
        # automation_paused=False ya excluye, de paso, toda conversación donde un humano tomó
        # el control (handoff a Sol, a un gerente, atención humana solicitada...): esos casos
        # siempre pausan el bot, así que nunca hace falta una lista aparte de "pasos donde no
        # insistir" — el bot simplemente no vuelve a hablar una vez que alguien más lo hace.
        candidates = db.query(Conversation).filter(
            Conversation.status != "closed",
            Conversation.automation_paused == False,  # noqa: E712
            Conversation.deleted_at.is_(None),
        ).all()
        for conv in candidates:
            # Una conversación que falla (Meta rechaza el envío: ventana de 24 h vencida, número
            # bloqueado...) antes abortaba la pasada entera: ninguna conversación posterior
            # recibía su seguimiento, y la misma fallaba contra Meta cada minuto para siempre.
            try:
                await _maybe_follow_up(db, conv)
            except Exception:
                db.rollback()
                logger.exception(f"[BotFollowup] No se pudo enviar el seguimiento a conv {conv.id}; no se reintenta para esta pausa.")
                try:
                    conv.bot_followup_sent_at = datetime.utcnow()
                    db.commit()
                except Exception:
                    db.rollback()
    finally:
        db.close()


def _last_visible_message(db, conv_id: int) -> Message | None:
    return db.query(Message).filter(
        Message.conversation_id == conv_id,
        Message.deleted_at.is_(None),
        Message.is_internal == False,  # noqa: E712
    ).order_by(Message.created_at.desc(), Message.id.desc()).first()


async def _maybe_follow_up(db, conv: Conversation) -> None:
    last_msg = _last_visible_message(db, conv.id)
    if not last_msg or last_msg.direction != "outgoing" or last_msg.sender_type != "system":
        # El cliente ya respondió (será el próximo procesamiento de webhook quien conteste,
        # no este loop), o quien habló último fue un agente humano, no el bot.
        return

    # Naive UTC a propósito, igual que en Conversation.needs_reminder: las columnas DATETIME
    # de MySQL no conservan tzinfo, comparar contra un datetime "aware" revienta.
    now = datetime.utcnow()
    threshold = now - timedelta(minutes=FOLLOWUP_THRESHOLD_MINUTES)
    if last_msg.created_at > threshold:
        return  # todavía no pasaron los 5 minutos
    if last_msg.created_at < now - timedelta(minutes=FOLLOWUP_MAX_AGE_MINUTES):
        # Un "¿sigues ahí?" horas o días después no reengancha a nadie (y pasadas 24 h Meta lo
        # rechaza). Solo aplica a pausas recientes; esto también evita barrer el historial viejo
        # de conversaciones que quedaron abiertas.
        return

    if conv.bot_followup_sent_at is not None and conv.bot_followup_sent_at >= last_msg.created_at:
        return  # ya se mandó el único seguimiento para esta pausa del cliente

    contact = conv.contact
    if not contact or not contact.phone:
        return

    # Import diferido: evita un ciclo de imports (routers.webhooks no necesita este módulo,
    # pero importar al nivel de módulo aquí arriba haría que cargar bot_followup dependiera de
    # que webhooks.py ya esté completamente inicializado).
    from routers.webhooks import _send_plain_text_message

    wa_service = get_whatsapp_service(conv.whatsapp_phone_number_id)
    text = get_node_text(db, "bot_followup_message", BOT_FOLLOWUP_MESSAGE)
    await _send_plain_text_message(db, wa_service, conv, contact, contact.phone, text)

    conv.bot_followup_sent_at = datetime.utcnow()
    db.commit()
    logger.info(f"[BotFollowup] Seguimiento enviado a conv {conv.id} tras {FOLLOWUP_THRESHOLD_MINUTES} min de silencio.")
