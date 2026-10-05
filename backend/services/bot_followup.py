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
from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import BOT_FOLLOWUP_ENABLED, BOT_FOLLOWUP_MESSAGE, HANDOFF_WAIT_MESSAGE
from services.flow_content import get_node_text
from services.push_service import notify_branch_staff
from services.whatsapp_service import get_whatsapp_service

logger = logging.getLogger("farmhouse.bot_followup")

FOLLOWUP_THRESHOLD_MINUTES = 5
# Cuando lo último que mandó el bot fue el enlace del Menú Digital (o la lista "¿algo más?" que
# va justo después), el cliente está armando el pedido en el menú: 5 minutos de silencio ahí
# es lo normal, no una pausa. Se espera bastante más antes de preguntar "¿sigues ahí?".
FOLLOWUP_MENU_THRESHOLD_MINUTES = 15
# Marcas en el contenido guardado de esos dos mensajes (ver _send_digital_menu_link y
# AFTER_MENU_HELP_QUESTION en routers/webhooks.py / services/auto_responses.py).
MENU_LINK_MARKER = "[Botón: Ver menú"
# Pasada esta antigüedad del último mensaje del bot ya no se manda el seguimiento.
FOLLOWUP_MAX_AGE_MINUTES = 60

# Cliente esperando a una persona: minutos desde el handoff del bot sin que ningún agente le
# haya escrito, antes de avisar a los encargados y decirle al cliente que siguen con él. Pasada
# la ventana máxima ya no tiene sentido (un aviso horas después no ayuda a nadie).
HANDOFF_ESCALATION_MINUTES = 10
HANDOFF_ESCALATION_MAX_AGE_MINUTES = 120
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
        await _escalate_unanswered_handoffs(db)
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
                    conv.bot_followup_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    minutes = FOLLOWUP_THRESHOLD_MINUTES
    content = last_msg.content or ""
    if MENU_LINK_MARKER in content or _after_menu_question(db) in content:
        minutes = FOLLOWUP_MENU_THRESHOLD_MINUTES   # está viendo el menú, no se le apura
    threshold = now - timedelta(minutes=minutes)
    if last_msg.created_at > threshold:
        return  # todavía no pasó el tiempo de espera
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

    conv.bot_followup_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    logger.info(f"[BotFollowup] Seguimiento enviado a conv {conv.id} tras {FOLLOWUP_THRESHOLD_MINUTES} min de silencio.")


def _after_menu_question(db) -> str:
    from services.auto_responses import AFTER_MENU_HELP_QUESTION
    return get_node_text(db, "after_menu_help_question", AFTER_MENU_HELP_QUESTION)


def _agent_replied_since(db, conv_id: int, since: datetime) -> bool:
    return db.query(Message.id).filter(
        Message.conversation_id == conv_id,
        Message.deleted_at.is_(None),
        Message.is_internal == False,  # noqa: E712
        Message.direction == "outgoing",
        Message.sender_type == "agent",
        Message.created_at >= since,
    ).first() is not None


async def _escalate_unanswered_handoffs(db) -> None:
    """Conversaciones que el bot le pasó a una persona hace HANDOFF_ESCALATION_MINUTES o más
    y en las que ningún agente ha escrito desde entonces: se avisa por push a los encargados
    de la sucursal y se le dice al cliente que siguen con él. Una sola vez por handoff."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    oldest = now - timedelta(minutes=HANDOFF_ESCALATION_MAX_AGE_MINUTES)
    limit = now - timedelta(minutes=HANDOFF_ESCALATION_MINUTES)
    candidates = db.query(Conversation).filter(
        Conversation.status != "closed",
        Conversation.deleted_at.is_(None),
        Conversation.automation_paused == True,  # noqa: E712
        Conversation.bot_handoff_at.isnot(None),
        Conversation.bot_handoff_at <= limit,
        Conversation.bot_handoff_at >= oldest,
    ).all()
    for conv in candidates:
        if conv.handoff_escalated_at is not None and conv.handoff_escalated_at >= conv.bot_handoff_at:
            continue
        try:
            if _agent_replied_since(db, conv.id, conv.bot_handoff_at):
                conv.handoff_escalated_at = now   # ya lo atendieron: no hay nada que escalar
                db.commit()
                continue
            contact = conv.contact
            nombre = (contact.name if contact and contact.name else None) or (contact.phone if contact else "Cliente")
            sucursal = conv.branch.name if conv.branch else "Sin sucursal"
            minutos = int((now - conv.bot_handoff_at).total_seconds() // 60)
            notify_branch_staff(
                db, conv.branch_id,
                f"Cliente esperando a una persona · {sucursal}",
                f"{nombre} lleva {minutos} min sin respuesta desde que pidió hablar con alguien.",
                f"/app?conversation_id={conv.id}", tag=f"fh-handoff-{conv.id}", managers_only=True,
            )
            if contact and contact.phone:
                from routers.webhooks import _send_plain_text_message
                wa_service = get_whatsapp_service(conv.whatsapp_phone_number_id)
                text = get_node_text(db, "handoff_wait_message", HANDOFF_WAIT_MESSAGE)
                await _send_plain_text_message(db, wa_service, conv, contact, contact.phone, text)
            conv.handoff_escalated_at = now
            db.commit()
            logger.info(f"[BotFollowup] Handoff sin respuesta escalado en conv {conv.id} ({minutos} min).")
        except Exception:
            db.rollback()
            logger.exception(f"[BotFollowup] No se pudo escalar el handoff de conv {conv.id}.")
            try:
                conv.handoff_escalated_at = now
                db.commit()
            except Exception:
                db.rollback()
