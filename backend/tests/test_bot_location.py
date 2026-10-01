"""
Delivery por ubicación: al elegir delivery el bot pide la ubicación con el botón nativo de
WhatsApp, y con la respuesta (mensaje tipo "location") recomienda y asigna la sucursal más
cercana. Muy lejos de todas, ofrece retirar. Escribir el nombre de la sucursal sigue valiendo.
"""
import pytest

from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import DELIVERY_LOCATION_REQUEST_BODY, DELIVERY_MAX_KM
from services.whatsapp_service import MockWhatsAppService
from tests.test_bot_new_flow import _post_bot_message

PHONE = "50769990077"
CERCA_DE_CLAYTON = (9.0050, -79.5700)
CERCA_DE_OBARRIO = (8.9870, -79.5190)
MUY_LEJOS = (8.4300, -80.0000)   # Penonomé, a más de 60 km


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: True)
    # Sin red en los tests: la ubicación en palabras se simula.
    async def _fake_geocode(lat, lng):
        return {"street": "Calle 50", "area": "Obarrio", "place": "", "label": "Calle 50, Obarrio"}
    monkeypatch.setattr("routers.webhooks.reverse_geocode", _fake_geocode)


def _post_location(client, phone, wamid, lat, lng, name=None):
    loc = {"latitude": lat, "longitude": lng}
    if name:
        loc["name"] = name
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": phone, "id": wamid, "timestamp": "1725500000", "type": "location", "location": loc}],
        }}]}],
    })


def _conv(db_session):
    contact = db_session.query(Contact).filter(Contact.phone == f"+{PHONE}").first()
    return db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()


def _outgoing(db_session, conv_id):
    return [m.content for m in db_session.query(Message).filter(Message.conversation_id == conv_id, Message.direction == "outgoing").order_by(Message.id).all()]


def test_el_parser_saca_las_coordenadas_de_una_ubicacion():
    parsed = MockWhatsAppService().parse_incoming_message({"entry": [{"changes": [{"value": {
        "messages": [{"from": PHONE, "id": "w1", "type": "location", "location": {"latitude": "9.0050", "longitude": "-79.57", "name": "Mi casa"}}],
    }}]}]})
    assert parsed["message_type"] == "location"
    assert parsed["location"] == {"latitude": 9.005, "longitude": -79.57, "name": "Mi casa", "address": None}
    assert "maps.google.com/?q=9.005,-79.57" in parsed["text"] and "Mi casa" in parsed["text"]


def test_elegir_delivery_pide_la_ubicacion_en_vez_de_la_lista(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L1", text="quiero delivery")
    conv = _conv(db_session)
    assert conv.delivery_type == "delivery" and conv.branch_id is None
    contenidos = _outgoing(db_session, conv.id)
    assert contenidos[-1] == f"📍 {DELIVERY_LOCATION_REQUEST_BODY}"
    assert not any("¿Desde cuál sucursal" in c for c in contenidos)


def test_la_ubicacion_asigna_la_sucursal_mas_cercana_y_sigue_con_el_menu(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L2", text="delivery")
    _post_location(client, PHONE, "wamid.L3", *CERCA_DE_CLAYTON, name="Mi casa")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id == clayton_branch.id
    contenidos = _outgoing(db_session, conv.id)
    assert any("Tu sucursal más cercana es *Clayton*" in c and " km" in c for c in contenidos)
    assert any("Te ubico en *Calle 50, Obarrio*" in c for c in contenidos)
    assert "dirección completa" in contenidos[-1]
    assert conv.delivery_intake_step == 1
    # Responde todo junto (tipo de lugar + referencia, una sola pregunta): recién ahí llega el
    # menú, sin una segunda pregunta de "¿cuál es la referencia?" de por medio.
    _post_bot_message(client, PHONE, "wamid.L3b", text="PH Torre Mar, apto 5B")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.delivery_intake_step is None and conv.delivery_place_type == "ph" and conv.delivery_reference == "PH Torre Mar, apto 5B"
    contenidos = _outgoing(db_session, conv.id)
    assert not any("¿Cómo se llama el PH" in c for c in contenidos)
    anotado = next(c for c in contenidos if "PH / edificio: PH Torre Mar, apto 5B" in c)
    # La confirmación y la info de la sucursal van en la misma burbuja, no en dos aparte.
    assert "Tu pedido sale de *Clayton*" in anotado
    assert any("/menu?" in c and "branch=CLY" in c for c in contenidos)
    contacto = db_session.query(Contact).filter(Contact.phone == f"+{PHONE}").one()
    assert (float(contacto.latitude), float(contacto.longitude)) == CERCA_DE_CLAYTON
    assert (contacto.address, contacto.building_or_house, contacto.address_reference) == ("Calle 50, Obarrio", "PH / edificio", "PH Torre Mar, apto 5B")   # sin full_address en el simulado: cae al label
    entrante = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "incoming", Message.content.like("%Ubicaci%")).first()
    assert "maps.google.com" in entrante.content and entrante.media_type is None


def test_la_sucursal_mas_cercana_cambia_segun_donde_este(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L4", text="delivery")
    _post_location(client, PHONE, "wamid.L5", *CERCA_DE_OBARRIO)
    db_session.expire_all()
    assert _conv(db_session).branch_id == obarrio_branch.id


def test_muy_lejos_ofrece_retirar_en_vez_de_delivery(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L6", text="delivery")
    _post_location(client, PHONE, "wamid.L7", *MUY_LEJOS)
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id is None and conv.delivery_type == "pickup"
    contenidos = _outgoing(db_session, conv.id)
    assert any(f"llega hasta {DELIVERY_MAX_KM} km" in c for c in contenidos)
    assert "retiras" in contenidos[-1]   # lista de sucursales para retiro


def test_escribir_la_sucursal_sigue_funcionando_tras_pedir_la_ubicacion(client, clayton_branch, obarrio_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L8", text="delivery")
    _post_bot_message(client, PHONE, "wamid.L9", text="obarrio")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id == obarrio_branch.id
    assert any("/menu?" in c for c in _outgoing(db_session, conv.id))


def test_texto_que_no_es_sucursal_cae_a_la_lista_de_siempre(client, clayton_branch, db_session):
    _post_bot_message(client, PHONE, "wamid.L10", text="delivery")
    _post_bot_message(client, PHONE, "wamid.L11", text="no se cual me queda cerca")
    conv = _conv(db_session)
    assert conv.branch_id is None
    assert "¿Desde cuál sucursal" in _outgoing(db_session, conv.id)[-1]


def test_ubicacion_sin_sucursales_con_coordenadas_cae_a_la_lista(client, db_session, clayton_branch):
    clayton_branch.latitude = None
    clayton_branch.longitude = None
    db_session.commit()
    _post_bot_message(client, PHONE, "wamid.L12", text="delivery")
    _post_location(client, PHONE, "wamid.L13", *CERCA_DE_CLAYTON)
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id is None
    assert "¿Desde cuál sucursal" in _outgoing(db_session, conv.id)[-1]


def test_menu_directo_con_delivery_va_de_la_ubicacion_al_menu_sin_preguntas(client, clayton_branch, obarrio_branch, db_session):
    # "Ver el menú y pedir" + delivery: la ubicación tiene que bastar para llegar al menú.
    # Se le dice cuánto cuesta el envío, pero no se le piden PH/apto ni referencia.
    _post_bot_message(client, PHONE, "wamid.MD1", button_id="main_menu_direct", button_title="Ver el menú y pedir")
    _post_bot_message(client, PHONE, "wamid.MD2", button_id="menu_direct_delivery", button_title="Delivery")
    _post_location(client, PHONE, "wamid.MD3", *CERCA_DE_CLAYTON, name="Mi casa")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.branch_id == clayton_branch.id and conv.delivery_type == "delivery"
    contenidos = _outgoing(db_session, conv.id)
    assert any("Tu sucursal más cercana es *Clayton*" in c for c in contenidos)
    assert any("El delivery hasta tu ubicación cuesta" in c for c in contenidos)   # el monto
    assert any("/menu?" in c and "branch=CLY" in c for c in contenidos)
    assert not any("dirección completa" in c for c in contenidos)
    # El chat no queda sin salida: tras el link siguen ofreciéndose opciones.
    assert "algo más en lo que pueda ayudarte" in contenidos[-1]


def test_el_menu_directo_no_deja_al_delivery_siguiente_sin_preguntas(client, clayton_branch, obarrio_branch, db_session):
    # La bandera del menú directo se consume al usarla. Si sobreviviera, el próximo delivery de
    # esta misma conversación se saltaría PH/apto y referencia, y el motorizado se quedaría sin
    # ellas: por eso este caso se prueba aparte del feliz.
    _post_bot_message(client, PHONE, "wamid.MD4", button_id="main_menu_direct", button_title="Ver el menú y pedir")
    _post_bot_message(client, PHONE, "wamid.MD5", button_id="menu_direct_delivery", button_title="Delivery")
    _post_location(client, PHONE, "wamid.MD6", *CERCA_DE_CLAYTON)
    db_session.expire_all()
    assert _conv(db_session).delivery_intake_step is None

    # Ahora un delivery normal: acá sí tienen que volver las preguntas de entrega.
    _post_bot_message(client, PHONE, "wamid.MD7", text="delivery")
    _post_location(client, PHONE, "wamid.MD8", *CERCA_DE_CLAYTON)
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.delivery_intake_step == 1
    assert "dirección completa" in _outgoing(db_session, conv.id)[-1]
