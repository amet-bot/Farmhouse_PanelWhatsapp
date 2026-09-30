"""
Datos de entrega tras la ubicación: la ubicación en palabras (OpenStreetMap), la pregunta
"¿PH, casa o local?" y la referencia; el horario real de cada sucursal en los mensajes.
"""
import pytest

from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services import geocoding
from services.branch_hours import hours_label
from services.auto_responses import match_delivery_place
from tests.test_bot_location import _post_location, CERCA_DE_CLAYTON
from tests.test_bot_new_flow import _post_bot_message

PHONE = "50769990088"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)
    monkeypatch.setattr("routers.webhooks.is_branch_open", lambda branch: True)

    async def _fake_geocode(lat, lng):
        return {"street": "Avenida Samuel Lewis", "area": "Obarrio", "place": "",
                "label": "Obarrio, Avenida Samuel Lewis, cerca de Parque Harry Strunz",
                "full_address": "Avenida Samuel Lewis, Obarrio, Bella Vista, Ciudad de Panamá",
                "landmark": {"name": "Parque Harry Strunz", "kind": "parque", "distance_m": 90},
                "landmark_text": "Parque Harry Strunz (parque, a 90 m)"}
    monkeypatch.setattr("routers.webhooks.reverse_geocode", _fake_geocode)


def _conv(db_session):
    contact = db_session.query(Contact).filter(Contact.phone == f"+{PHONE}").first()
    return db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()


def _outgoing(db_session, conv_id):
    return [m.content for m in db_session.query(Message).filter(Message.conversation_id == conv_id, Message.direction == "outgoing").order_by(Message.id).all()]


def _start_delivery_with_location(client):
    _post_bot_message(client, PHONE, "wamid.D1", text="delivery")
    _post_location(client, PHONE, "wamid.D2", *CERCA_DE_CLAYTON)


def test_describe_arma_la_frase_lugar_calle_y_referencia():
    d = geocoding.describe({"road": "Avenida Samuel Lewis", "neighbourhood": "Obarrio", "suburb": "Bella Vista", "county": "Distrito de Panamá", "shop": "La Cuisine"}, "La Cuisine", "shop")
    assert d["label"] == "Obarrio, Avenida Samuel Lewis, en La Cuisine" and d["place"] == "La Cuisine"
    assert d["full_address"] == "Avenida Samuel Lewis, Obarrio, Bella Vista, Ciudad de Panamá"
    d = geocoding.describe({"road": "Calle 50", "house_number": "12", "suburb": "Bella Vista"}, "Calle 50", "highway")
    assert d["label"] == "Bella Vista, Calle 50 12" and d["place"] == ""
    assert geocoding.describe({}, None, None)["label"] == ""


def _way(name, pts, **tags):
    return {"type": "way", "tags": {"name": name, **tags}, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


def test_la_calle_mas_cercana_es_la_del_pin_no_la_del_edificio():
    lat, lng = 9.0000, -79.5000
    # Calle E pasa a ~20 m al norte del pin; Calle H a ~150 m (fuera del radio).
    elements = [
        _way("Calle H", [(lat + 0.00135, lng - 0.001), (lat + 0.00135, lng + 0.001)], highway="residential"),
        _way("Calle E", [(lat + 0.00018, lng - 0.001), (lat + 0.00018, lng + 0.001)], highway="residential"),
        {"type": "node", "lat": lat + 0.0008, "lon": lng, "tags": {"name": "Escuela Paraíso", "amenity": "school"}},
        {"type": "node", "lat": lat + 0.0002, "lon": lng + 0.0002, "tags": {"name": "Banco X", "amenity": "bank"}},
    ]
    street = geocoding.nearest_street(elements, lat, lng)
    assert street["name"] == "Calle E" and street["distance_m"] <= 25
    landmark = geocoding.nearest_landmark(elements, lat, lng)
    # El banco está más cerca, pero la escuela orienta más (prioridad) y sigue a menos de 100 m.
    assert landmark["name"] == "Escuela Paraíso" and landmark["kind"] == "escuela"
    d = geocoding.compose({"road": "Calle H", "neighbourhood": "Paraíso", "suburb": "San Miguelito", "city": "Panamá"}, None, None, street=street, landmark=landmark)
    assert d["label"] == "Paraíso, Calle E, cerca de Escuela Paraíso"
    assert d["full_address"] == "Calle E, Paraíso, San Miguelito, Ciudad de Panamá"
    assert d["landmark_text"].startswith("Escuela Paraíso (escuela, a ")


def test_match_delivery_place():
    assert match_delivery_place("es un ph") == "ph"
    assert match_delivery_place("Torre Mar apto 5B") == "ph"
    assert match_delivery_place("una casa") == "casa"
    assert match_delivery_place("es la oficina del piso 3") == "ph" or match_delivery_place("oficinas delta") == "local"
    assert match_delivery_place("no se") is None


def test_casa_con_referencia_en_la_misma_frase_no_pregunta_dos_veces(client, clayton_branch, db_session):
    _start_delivery_with_location(client)
    _post_bot_message(client, PHONE, "wamid.D3", text="es una casa, la 12 de portón negro")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.delivery_place_type == "casa" and conv.delivery_intake_step is None
    assert conv.delivery_reference == "es una casa, la 12 de portón negro"
    contenidos = _outgoing(db_session, conv.id)
    assert not any("¿Número de casa" in c for c in contenidos)
    assert any("Casa: es una casa, la 12 de portón negro" in c for c in contenidos)
    assert "/menu?" in "".join(contenidos)


def test_referencia_directa_sin_decir_el_tipo_tambien_vale(client, clayton_branch, db_session):
    _start_delivery_with_location(client)
    _post_bot_message(client, PHONE, "wamid.D4", text="frente al parque, cerca de la farmacia")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.delivery_intake_step is None and conv.delivery_place_type is None
    assert conv.delivery_reference == "frente al parque, cerca de la farmacia"
    assert any("Entrega: frente al parque" in c for c in _outgoing(db_session, conv.id))


def test_el_resumen_del_handoff_trae_la_entrega_completa(client, clayton_branch, db_session):
    _start_delivery_with_location(client)
    _post_bot_message(client, PHONE, "wamid.D5", button_id="place_local")
    _post_bot_message(client, PHONE, "wamid.D6", text="Oficinas Delta, piso 3")
    _post_bot_message(client, PHONE, "wamid.D7", text="quiero hablar con alguien")
    conv = _conv(db_session)
    resumen = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.is_internal == True).order_by(Message.id.desc()).first()  # noqa: E712
    assert "• Entrega: Local / oficina · Oficinas Delta, piso 3" in resumen.content
    nota = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.is_internal == True, Message.content.like("📍 Ubicación del cliente%")).one()  # noqa: E712
    assert "Obarrio, Avenida Samuel Lewis, cerca de Parque Harry Strunz" in nota.content
    assert "• Referencia cercana: Parque Harry Strunz (parque, a 90 m)" in nota.content
    assert "• Dirección para PedidosYa: Avenida Samuel Lewis, Obarrio, Bella Vista, Ciudad de Panamá" in nota.content
    assert "• Pin exacto: 9.005000, -79.570000" in nota.content
    assert "• Dirección para PedidosYa: Avenida Samuel Lewis, Obarrio, Bella Vista, Ciudad de Panamá" in resumen.content
    assert "• Pin exacto: 9.005000, -79.570000 · https://maps.google.com/?q=9.005,-79.57" in resumen.content


def test_empezar_de_nuevo_limpia_las_preguntas_de_entrega(client, clayton_branch, db_session):
    _start_delivery_with_location(client)
    _post_bot_message(client, PHONE, "wamid.D8", text="empezar de nuevo")
    db_session.expire_all()
    conv = _conv(db_session)
    assert conv.delivery_intake_step is None and conv.branch_id is None


def test_sin_geocodificacion_igual_pregunta_el_lugar(client, clayton_branch, db_session, monkeypatch):
    async def _none(lat, lng):
        return None
    monkeypatch.setattr("routers.webhooks.reverse_geocode", _none)
    _start_delivery_with_location(client)
    conv = _conv(db_session)
    contenidos = _outgoing(db_session, conv.id)
    assert any("Tu sucursal más cercana es *Clayton*" in c and "Te ubico" not in c for c in contenidos)
    assert "¿a qué tipo de lugar" in contenidos[-1]


def test_horario_real_por_sucursal_en_los_mensajes(client, clayton_branch, obarrio_branch, db_session):
    obarrio_branch.opens_at = "06:00"
    db_session.commit()
    assert hours_label(clayton_branch) == "Lunes a Domingo: 8:00 AM - 9:30 PM"
    assert hours_label(obarrio_branch) == "Lunes a Domingo: 6:00 AM - 9:30 PM"
    _post_bot_message(client, PHONE, "wamid.D9", text="retiro")
    _post_bot_message(client, PHONE, "wamid.D10", text="obarrio")
    conv = _conv(db_session)
    assert any("🕒 Lunes a Domingo: 6:00 AM - 9:30 PM" in c for c in _outgoing(db_session, conv.id))
