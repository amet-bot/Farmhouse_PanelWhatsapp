"""
Seguimiento automático del bot cuando el cliente deja de responder (services/bot_followup.py):
si el bot fue quien habló último y pasaron >= 5 minutos sin respuesta, se manda un único mensaje
de reenganche. No insiste una segunda vez para la misma pausa del cliente.

No hay pytest-asyncio configurado en el proyecto (ningún otro test corre código async
directamente; los que ejercitan el bot pasan por TestClient, que maneja el async por dentro),
así que estas pruebas usan asyncio.run() para llamar a _sweep_once() desde una función de test
normal, sin agregar una dependencia nueva.
"""
import asyncio
from datetime import datetime, timedelta

from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import BOT_FOLLOWUP_MESSAGE
from services.bot_followup import FOLLOWUP_THRESHOLD_MINUTES, _sweep_once


def setup_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    # _sweep_once abre su propia sesión (no la del request), igual que _process_auto_flow_background
    # en routers/webhooks.py — hay que apuntarla a la base de datos de prueba en memoria.
    monkeypatch.setattr("services.bot_followup.SessionLocal", TestingSessionLocal)


def _make_conversation(db_session, *, status="open", automation_paused=False):
    contact = Contact(name="Cliente Prueba", phone="+50761112222")
    db_session.add(contact)
    db_session.commit()
    db_session.refresh(contact)

    conv = Conversation(
        customer_id=contact.id, status=status, automation_paused=automation_paused,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _add_message(db_session, conv, *, direction, sender_type, created_at):
    msg = Message(
        conversation_id=conv.id, direction=direction, sender_type=sender_type,
        content="hola", is_internal=False, created_at=created_at,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def _followup_messages(db_session, conv_id):
    """Solo los mensajes que de verdad son EL seguimiento — no cualquier mensaje de sistema,
    porque los tests también insertan mensajes 'system' para simular que el bot ya había
    hablado antes; contar todos los 'system' contaría siempre uno de más."""
    return db_session.query(Message).filter(
        Message.conversation_id == conv_id, Message.content.contains(BOT_FOLLOWUP_MESSAGE),
    ).all()


def test_sends_followup_after_threshold_when_bot_spoke_last(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=FOLLOWUP_THRESHOLD_MINUTES + 1),
    )

    asyncio.run(_sweep_once())

    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is not None
    sent = _followup_messages(db_session, conv.id)
    assert any(BOT_FOLLOWUP_MESSAGE in m.content for m in sent)


def test_does_not_send_before_threshold(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=1),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is None


def test_does_not_send_when_automation_paused(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session, automation_paused=True)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is None


def test_does_not_send_when_conversation_closed(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session, status="closed")
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is None


def test_does_not_send_when_customer_already_replied(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    _add_message(
        db_session, conv, direction="incoming", sender_type="customer",
        created_at=datetime.utcnow() - timedelta(minutes=1),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is None


def test_only_sends_once_per_silence_episode(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    first_sent_at = conv.bot_followup_sent_at
    assert first_sent_at is not None

    # Segunda pasada sin que el cliente haya escrito nada más: no debe insistir de nuevo.
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at == first_sent_at
    assert len(_followup_messages(db_session, conv.id)) == 1


def test_disabled_switch_sends_nothing(db_session, monkeypatch):
    """Interruptor de emergencia (BOT_FOLLOWUP_ENABLED): con él en False, el sweep no manda
    nada aunque un candidato califique de sobra. Existe para poder apagarlo de un momento a
    otro (edita el archivo y reinicia) sin esperar un deploy si algo se ve mal en producción."""
    setup_env(monkeypatch)
    monkeypatch.setattr("services.bot_followup.BOT_FOLLOWUP_ENABLED", False)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is None


def test_sends_again_after_a_new_silence_episode(db_session, monkeypatch):
    setup_env(monkeypatch)
    conv = _make_conversation(db_session)
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert conv.bot_followup_sent_at is not None

    # La prueba corre casi instantánea (a diferencia de la vida real, donde pasarían minutos
    # de verdad): sin esto, el seguimiento recién mandado seguiría viéndose "de ahora mismo" y
    # nunca calificaría como una pausa vieja. Se empuja 20 minutos al pasado a mano, tanto el
    # mensaje como el timestamp guardado en la conversación, para simular que sí pasó un rato.
    followup_msg = _followup_messages(db_session, conv.id)[0]
    followup_msg.created_at = datetime.utcnow() - timedelta(minutes=20)
    conv.bot_followup_sent_at = followup_msg.created_at
    db_session.add(followup_msg)
    db_session.add(conv)
    db_session.commit()

    # El cliente respondió, el bot le volvió a hablar, y ahora pasan otros 6 minutos callado.
    _add_message(
        db_session, conv, direction="incoming", sender_type="customer",
        created_at=datetime.utcnow() - timedelta(minutes=8),
    )
    _add_message(
        db_session, conv, direction="outgoing", sender_type="system",
        created_at=datetime.utcnow() - timedelta(minutes=6),
    )
    asyncio.run(_sweep_once())
    db_session.refresh(conv)
    assert len(_followup_messages(db_session, conv.id)) == 2
