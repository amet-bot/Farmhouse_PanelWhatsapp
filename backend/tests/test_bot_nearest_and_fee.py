"""
"📍 La más cercana a mí" en retiro/visita (ubicación -> sucursales ordenadas por distancia, el
cliente elige) y la tarifa de delivery por distancia dicha apenas se conoce la sucursal.
"""
import pytest

from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.whatsapp_service import MockWhatsAppService
from tests.test_bot_location import _post_location, CERCA_DE_CLAYTON, CERCA_DE_OBARRIO
from tests.test_bot_new_flow import _post_bot_message

PHONE = "50769990099"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: True)

    async def _fake_geocode(lat, lng):
        return None
    monkeypatch.setattr("routers.webhooks.reverse_geocode", _fake_geocode)


@pytest.fixture
def listas(monkeypatch):
    """Registra las filas de cada lista interactiva que manda el bot (el contenido guardado
    solo trae el texto, no las filas)."""
    enviadas = []
    original = MockWhatsAppService.send_interactive_list

    async def grabar(self, to_phone, body_text, button_text, rows, section_title="Sucursales Farmhouse"):
        enviadas.append({"body": body_text, "rows": rows})
        return await original(self, to_phone, body_text, button_text, rows, section_title)
    monkeypatch.setattr(MockWhatsAppService, "send_interactive_list", grabar)
    return enviadas


def _conv(db_session):
    contact = db_session.query(Contact).filter(Contact.phone == f"+{PHONE}").first()
    return db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()


def _outgoing(db_session, conv_id):
    return [m.content for m in db_session.query(Message).filter(Message.conversation_id == conv_id, Message.direction == "outgoing").order_by(Message.id).all()]


def test_la_lista_de_sucursales_de_retiro_ofrece_la_mas_cercana(client, clayton_branch, obarrio_branch, db_session, listas):
    _post_bot_message(client, PHONE, "wamid.N1", text="retiro")
    lista = listas[-1]
    assert "¿En cuál sucursal retiras?" in lista["body"]
    ids = [r["id"] for r in lista["rows"]]
    assert ids[0] == "branch_nearest" and ids[-1] == "nav_restart"
    assert f"branch_{clayton_branch.id}" in ids and f"branch_{obarrio_branch.id}" in ids


def test_retiro_con_ubicacion_dice_la_mas_cercana_y_deja_elegir(client, clayton_branch, obarrio_branch, db_session, listas):
    _post_bot_message(client, PHONE, "wamid.N2", text="retiro")
    _post_interactive_list(client, PHONE, "wamid.N3", "branch_nearest")
    contenidos = _outgoing(db_session, _conv(db_session).id)
    assert "cuál sucursal te queda más cerca" in contenidos[-1]

    _post_location(client, PHONE, "wamid.N4", *CERCA_DE_OBARRIO)
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id is None and conv.delivery_type == "pickup"
    contenidos = _outgoing(db_session, conv.id)
    assert any("Te queda más cerca *Obarrio*" in c for c in contenidos)
    lista = listas[-1]
    rows = [r for r in lista["rows"] if r["id"].startswith("branch_") and r["id"] != "branch_nearest"]
    assert rows[0]["id"] == f"branch_{obarrio_branch.id}" and "km de ti" in rows[0]["description"]
    assert rows[1]["id"] == f"branch_{clayton_branch.id}"
    assert not any(r["id"] == "branch_nearest" for r in lista["rows"])

    # Elige y sigue el flujo de retiro de siempre.
    _post_interactive_list(client, PHONE, "wamid.N5", f"branch_{obarrio_branch.id}")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id == obarrio_branch.id
    assert any("/menu?" in c and "branch=OBR" in c for c in _outgoing(db_session, conv.id))


def test_delivery_con_ubicacion_dice_la_tarifa(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.N6", text="delivery")
    _post_location(client, PHONE, "wamid.N7", *CERCA_DE_CLAYTON)   # a menos de 1 km de Clayton
    conv = _conv(db_session)
    contenidos = _outgoing(db_session, conv.id)
    assert any("Tu sucursal más cercana es *Clayton*" in c and "cuesta *$5.00*" in c for c in contenidos)


def test_tarifa_de_10_entre_3_y_5_km(client, clayton_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.N8", text="delivery")
    # ~4 km al este de Clayton
    _post_location(client, PHONE, "wamid.N9", 9.0038590, -79.5730430 + 0.0364)
    conv = _conv(db_session)
    assert any("cuesta *$10.00*" in c for c in _outgoing(db_session, conv.id))


def test_la_lista_de_delivery_sin_ubicacion_tambien_ofrece_la_mas_cercana(client, clayton_branch, db_session, listas):
    _post_bot_message(client, PHONE, "wamid.N10", text="delivery")
    _post_bot_message(client, PHONE, "wamid.N11", text="no se")
    assert listas[-1]["rows"][0]["id"] == "branch_nearest"


def _post_interactive_list(client, phone, wamid, row_id):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": phone, "id": wamid, "timestamp": "1725500000", "type": "interactive",
                          "interactive": {"type": "list_reply", "list_reply": {"id": row_id, "title": row_id}}}],
        }}]}],
    })
