"""
Mejoras del bot (2026-09-30): sesión que vence, aviso de sucursal cerrada, "para llevar" es
retiro, resumen del pedido por chat, "¿sigues ahí?" más paciente con el menú abierto,
escalamiento de handoffs sin respuesta, "Cobrar con Yappy" desde el panel y el embudo.
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta

import pytest

from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.order import Order
from services import bot_followup, faq_bot
from services.auto_responses import (
    BOT_FOLLOWUP_MESSAGE, BOT_SESSION_TIMEOUT_HOURS, ENTRY_GATE_BODY, HANDOFF_WAIT_MESSAGE,
)
from services.order_flow_matcher import match_delivery_type_text, match_main_option
from tests.conftest import auth_headers_for
from tests.test_bot_new_flow import _post_bot_message

PHONE = "50769990001"


@pytest.fixture(autouse=True)
def _webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)
    monkeypatch.setattr("services.bot_followup.SessionLocal", TestingSessionLocal)
    # Fuera de los tests de "cerrado", la sucursal siempre está abierta.
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: True)


def _conv(db_session):
    contact = db_session.query(Contact).filter(Contact.phone == f"+{PHONE}").first()
    return db_session.query(Conversation).filter(Conversation.customer_id == contact.id).order_by(Conversation.id.desc()).first()


def _outgoing(db_session, conv_id):
    return db_session.query(Message).filter(Message.conversation_id == conv_id, Message.direction == "outgoing").order_by(Message.id).all()


# ---- palabras clave -----------------------------------------------------------------------

def test_para_llevar_es_retiro_no_delivery():
    assert match_delivery_type_text("quiero algo para llevar") == "pickup"
    assert match_main_option("un bowl para llevar") == "pickup"
    assert match_delivery_type_text("me lo pueden llevar a la casa") == "delivery"


def test_horario_de_delivery_no_manda_a_ver_sucursales():
    assert match_main_option("cual es el horario de delivery?") == "delivery"
    assert match_main_option("horario de la sucursal") == "visit"


# ---- sesión vencida -----------------------------------------------------------------------

def test_sesion_vencida_reinicia_el_bot_aunque_estuviera_pausado(client, clayton_branch, db_session):
    # Cliente que pidió hace días: bot pausado (handoff), sucursal y entrega viejas.
    _post_bot_message(client, PHONE, "wamid.S1", text="hola")
    conv = _conv(db_session)
    conv.automation_paused = True
    conv.bot_handoff_at = datetime.utcnow() - timedelta(hours=BOT_SESSION_TIMEOUT_HOURS + 2)
    conv.branch_id = clayton_branch.id
    conv.delivery_type = "delivery"
    conv.payment_method = "yappy"
    old = datetime.utcnow() - timedelta(hours=BOT_SESSION_TIMEOUT_HOURS + 1)
    for m in db_session.query(Message).filter(Message.conversation_id == conv.id).all():
        m.created_at = old
    db_session.commit()

    _post_bot_message(client, PHONE, "wamid.S2", text="hola, quiero pedir")

    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.automation_paused is False
    assert conv.delivery_type is None and conv.payment_method is None and conv.bot_handoff_at is None
    contenidos = [m.content for m in _outgoing(db_session, conv.id)]
    assert any("Nueva sesión" in c for c in contenidos)
    assert any(ENTRY_GATE_BODY.split("\n")[0] in c or "Soy el asistente" in c for c in contenidos[-2:])


def test_sin_pausa_larga_no_se_reinicia_nada(client, clayton_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.S3", text="hola")
    _post_bot_message(client, PHONE, "wamid.S4", button_id="entry_gate_bot")
    conv = _conv(db_session)
    assert conv.last_branch_prompt_at is not None
    assert not any("Nueva sesión" in m.content for m in _outgoing(db_session, conv.id))


# ---- sucursal cerrada -----------------------------------------------------------------------

def test_cerrado_no_interrumpe_el_camino_directo_al_menu(client, clayton_branch, db_session, monkeypatch):
    """El aviso de "cerrado" dejó de salir automático pegado al link del menú (conversación del
    2026-10-01): igual puede armar y programar su pedido desde el Menú Digital. Se reserva para
    cuando pregunta el horario explícitamente (ver el test de abajo)."""
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: False)
    _post_bot_message(client, PHONE, "wamid.C1", text="delivery")
    _post_bot_message(client, PHONE, "wamid.C2", text="clayton")
    conv = _conv(db_session)
    contenidos = [m.content for m in _outgoing(db_session, conv.id)]
    assert not any("estamos cerrados" in c for c in contenidos)
    assert any("/menu?" in c for c in contenidos)


def test_aviso_de_cerrado_al_preguntar_el_horario(client, clayton_branch, db_session, monkeypatch):
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: False)
    _post_bot_message(client, PHONE, "wamid.C3", text="delivery")
    _post_bot_message(client, PHONE, "wamid.C4", text="clayton")
    _post_bot_message(client, PHONE, "wamid.C5", button_id="branch_hours")
    conv = _conv(db_session)
    contenidos = [m.content for m in _outgoing(db_session, conv.id)]
    cerrado = next((i for i, c in enumerate(contenidos) if "estamos cerrados" in c), None)
    assert cerrado is not None
    assert "8:00 AM" in contenidos[cerrado]


def test_sucursal_con_horario_propio_y_horas_de_corte():
    from services.branch_hours import is_branch_open, branch_opening_label
    from zoneinfo import ZoneInfo

    class B:
        opens_at, closes_at = "11:00", "22:00"

    tz = ZoneInfo("America/Panama")
    assert is_branch_open(B, datetime(2026, 9, 30, 12, 0, tzinfo=tz))
    assert not is_branch_open(B, datetime(2026, 9, 30, 23, 0, tzinfo=tz))
    assert not is_branch_open(B, datetime(2026, 9, 30, 10, 59, tzinfo=tz))
    assert branch_opening_label(B) == "11:00 AM"

    class Nocturna:
        opens_at, closes_at = "18:00", "02:00"
    assert is_branch_open(Nocturna, datetime(2026, 9, 30, 1, 0, tzinfo=tz))
    assert not is_branch_open(Nocturna, datetime(2026, 9, 30, 12, 0, tzinfo=tz))


def test_horario_de_sucursal_editable_por_api(client, admin_user, clayton_branch):
    h = auth_headers_for(admin_user)
    r = client.put(f"/api/branches/{clayton_branch.id}", json={"opens_at": "11:00", "closes_at": "20:00"}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["opens_at"], r.json()["closes_at"]) == ("11:00", "20:00")
    assert client.put(f"/api/branches/{clayton_branch.id}", json={"opens_at": "25:00"}, headers=h).status_code == 422


# ---- pedido por chat -------------------------------------------------------------------------

def test_lo_que_pidio_por_chat_va_en_el_resumen_del_handoff(client, clayton_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.P1", text="retiro")
    _post_bot_message(client, PHONE, "wamid.P2", text="clayton")
    _post_bot_message(client, PHONE, "wamid.P3", button_id="chat_order_start")
    _post_bot_message(client, PHONE, "wamid.P4", text="2 bowls de pollo y un smoothie de mango")
    _post_bot_message(client, PHONE, "wamid.P5", button_id="pay_yappy")
    conv = _conv(db_session)
    assert conv.chat_order_description == "2 bowls de pollo y un smoothie de mango"
    assert conv.automation_paused is True and conv.bot_handoff_at is not None
    resumen = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.is_internal == True).order_by(Message.id.desc()).first()  # noqa: E712
    assert "Pedido por chat: 2 bowls de pollo y un smoothie de mango" in resumen.content


# ---- seguimiento y escalamiento ----------------------------------------------------------------

def _paused_conv(db_session, *, phone="+50761110009", branch=None, handoff_minutes_ago=None):
    contact = Contact(name="Cliente Espera", phone=phone)
    db_session.add(contact); db_session.commit(); db_session.refresh(contact)
    conv = Conversation(customer_id=contact.id, status="open", branch_id=branch.id if branch else None,
                        automation_paused=handoff_minutes_ago is not None,
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    if handoff_minutes_ago is not None:
        conv.bot_handoff_at = datetime.utcnow() - timedelta(minutes=handoff_minutes_ago)
    db_session.add(conv); db_session.commit(); db_session.refresh(conv)
    return conv


def _msg(db_session, conv, content, *, sender_type="system", direction="outgoing", minutes_ago=0):
    m = Message(conversation_id=conv.id, direction=direction, sender_type=sender_type, content=content,
                is_internal=False, created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db_session.add(m); db_session.commit()
    return m


def test_sigues_ahi_espera_mas_cuando_el_cliente_esta_viendo_el_menu(db_session):
    conv = _paused_conv(db_session)
    _msg(db_session, conv, "Aquí tienes el menú\n\n[Botón: Ver menú y pedir] → https://x/menu?x=1", minutes_ago=8)
    asyncio.run(bot_followup._sweep_once())
    db_session.expire_all()
    assert not any(BOT_FOLLOWUP_MESSAGE in m.content for m in _outgoing(db_session, conv.id))

    for m in db_session.query(Message).filter(Message.conversation_id == conv.id).all():
        m.created_at = datetime.utcnow() - timedelta(minutes=bot_followup.FOLLOWUP_MENU_THRESHOLD_MINUTES + 1)
    db_session.commit()
    asyncio.run(bot_followup._sweep_once())
    db_session.expire_all()
    assert any(BOT_FOLLOWUP_MESSAGE in m.content for m in _outgoing(db_session, conv.id))


def test_handoff_sin_respuesta_avisa_a_encargados_y_al_cliente(db_session, clayton_branch, monkeypatch):
    avisos = []
    monkeypatch.setattr("services.bot_followup.notify_branch_staff", lambda db, branch_id, title, body, url, **kw: avisos.append((branch_id, title, kw)) or 1)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=bot_followup.HANDOFF_ESCALATION_MINUTES + 1)
    _msg(db_session, conv, "Claro, ya compartí tu solicitud", minutes_ago=bot_followup.HANDOFF_ESCALATION_MINUTES + 1)

    asyncio.run(bot_followup._sweep_once())
    db_session.expire_all()
    conv = db_session.get(Conversation, conv.id)
    assert conv.handoff_escalated_at is not None
    assert any(HANDOFF_WAIT_MESSAGE in m.content for m in _outgoing(db_session, conv.id))
    assert avisos and avisos[0][0] == clayton_branch.id and avisos[0][2]["managers_only"] is True
    assert "esperando a una persona" in avisos[0][1]

    # Una segunda pasada no vuelve a avisar.
    asyncio.run(bot_followup._sweep_once())
    db_session.expire_all()
    assert sum(HANDOFF_WAIT_MESSAGE in m.content for m in _outgoing(db_session, conv.id)) == 1 and len(avisos) == 1


def test_handoff_atendido_por_un_agente_no_escala(db_session, clayton_branch, monkeypatch):
    avisos = []
    monkeypatch.setattr("services.bot_followup.notify_branch_staff", lambda *a, **k: avisos.append(1) or 1)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=30)
    _msg(db_session, conv, "Hola, soy Ana de Clayton", sender_type="agent", minutes_ago=20)
    asyncio.run(bot_followup._sweep_once())
    db_session.expire_all()
    assert not avisos
    assert not any(HANDOFF_WAIT_MESSAGE in m.content for m in _outgoing(db_session, conv.id))


def test_handoff_muy_reciente_todavia_no_escala(db_session, clayton_branch, monkeypatch):
    avisos = []
    monkeypatch.setattr("services.bot_followup.notify_branch_staff", lambda *a, **k: avisos.append(1) or 1)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=3)
    asyncio.run(bot_followup._sweep_once())
    assert not avisos


# ---- cobrar con Yappy desde el panel -----------------------------------------------------------

def _enable_yappy(monkeypatch):
    monkeypatch.setattr(settings, "YAPPY_ENABLED", True)
    monkeypatch.setattr(settings, "YAPPY_MERCHANT_ID", "merchant-test")
    monkeypatch.setattr(settings, "YAPPY_SECRET_KEY", base64.b64encode(b"firma.x").decode())
    monkeypatch.setattr(settings, "YAPPY_DOMAIN", "https://farmhouse.example")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://farmhouse.example")


def test_cobrar_con_yappy_crea_el_pedido_y_manda_el_boton(client, db_session, clayton_branch, supervisor_user, clayton_device, monkeypatch):
    _enable_yappy(monkeypatch)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=2)
    conv.delivery_type = "pickup"
    conv.chat_order_description = "2 bowls de pollo"
    db_session.commit()
    h = auth_headers_for(supervisor_user, clayton_device.device_id)

    r = client.post("/api/payments/yappy/charge", json={"conversation_id": conv.id, "total": "18.50", "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == "18.50" and data["payment_url"].startswith("https://farmhouse.example/pago-yappy?order=FH-")
    order = db_session.query(Order).filter(Order.order_code == data["order_code"]).one()
    assert (order.source, order.order_type, order.payment_status, str(order.total)) == ("chat", "takeout", "pending", "18.50")
    assert json.loads(order.items_json)["description"] == "2 bowls de pollo"
    msg = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.sender_type == "agent").one()
    assert data["payment_url"] in msg.content and msg.status == "sent"
    db_session.expire_all()
    assert db_session.get(Conversation, conv.id).payment_method == "yappy"

    # El enlace es válido para la página de pago.
    token = data["payment_url"].split("token=", 1)[1]
    assert client.get(f"/api/payments/yappy/orders/{data['order_code']}?token={token}").json()["total"] == "18.50"


def test_cobrar_con_yappy_sin_yappy_configurado(client, db_session, clayton_branch, supervisor_user, clayton_device, monkeypatch):
    monkeypatch.setattr(settings, "YAPPY_ENABLED", False)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=2)
    h = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.post("/api/payments/yappy/charge", json={"conversation_id": conv.id, "total": "5.00"}, headers=h).status_code == 503


def test_cobrar_con_yappy_requiere_monto_valido(client, db_session, clayton_branch, supervisor_user, clayton_device, monkeypatch):
    _enable_yappy(monkeypatch)
    conv = _paused_conv(db_session, branch=clayton_branch, handoff_minutes_ago=2)
    h = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.post("/api/payments/yappy/charge", json={"conversation_id": conv.id, "total": "0"}, headers=h).status_code == 422


# ---- embudo y FAQ ----------------------------------------------------------------------------

def test_embudo_del_bot(client, db_session, clayton_branch, admin_user, clayton_agent, clayton_device):
    _post_bot_message(client, PHONE, "wamid.F1", text="delivery")
    _post_bot_message(client, PHONE, "wamid.F2", text="clayton")
    r = client.get("/api/bot/funnel?days=30", headers=auth_headers_for(admin_user))
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["started"] >= 1 and f["chose_option"] >= 1 and f["with_branch"] >= 1 and f["menu_sent"] >= 1
    assert set(f) >= {"orders", "paid", "handoffs", "handoffs_escalated"}
    assert client.get("/api/bot/funnel", headers=auth_headers_for(clayton_agent, clayton_device.device_id)).status_code == 403


def test_faq_usa_la_tarjeta_editable_e_ignora_el_ejemplo():
    assert "Parqueo: gratis" in faq_bot.build_system_prompt("Parqueo: gratis en la torre.")
    assert "Escribe aquí" not in faq_bot.build_system_prompt(faq_bot.FAQ_CONTEXT_DEFAULT)
    assert faq_bot.build_system_prompt("") == faq_bot.SYSTEM_PROMPT
