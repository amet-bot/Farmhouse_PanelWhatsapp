"""
El Menú Digital abre con la ubicación que el cliente compartió por WhatsApp: GET
/orders/public/prefill devuelve pin, dirección y referencia solo con el token de sesión firmado.
"""
from models.contact import Contact
from models.conversation import Conversation
from security.auth import create_menu_session_token


def _conv(db_session, branch, **contact_fields):
    contact = Contact(name="Cliente Mapa", phone="+50761230000", **contact_fields)
    db_session.add(contact); db_session.commit(); db_session.refresh(contact)
    conv = Conversation(customer_id=contact.id, branch_id=branch.id, status="open", delivery_type="delivery",
                        delivery_place_type="ph", delivery_reference="PH Torre Mar, apto 5B")
    db_session.add(conv); db_session.commit(); db_session.refresh(conv)
    return conv


def test_prefill_devuelve_la_ubicacion_del_contacto(client, db_session, clayton_branch):
    conv = _conv(db_session, clayton_branch, latitude=9.005, longitude=-79.57, address="Calle 50, Obarrio, Bella Vista, Ciudad de Panamá",
                 building_or_house="PH / edificio", address_reference="PH Torre Mar, apto 5B")
    token = create_menu_session_token(conv.id, clayton_branch.id)
    r = client.get(f"/api/orders/public/prefill?session={token}")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "delivery_type": "delivery", "latitude": 9.005, "longitude": -79.57,
        "address": "Calle 50, Obarrio, Bella Vista, Ciudad de Panamá", "building": "PH / edificio",
        "reference": "PH Torre Mar, apto 5B", "place_type": "ph",
    }


def test_prefill_sin_ubicacion_ni_token_valido(client, db_session, clayton_branch):
    conv = _conv(db_session, clayton_branch)
    token = create_menu_session_token(conv.id, clayton_branch.id)
    r = client.get(f"/api/orders/public/prefill?session={token}").json()
    assert r["latitude"] is None and r["address"] is None
    assert client.get("/api/orders/public/prefill?session=token-falso-1234").status_code == 401
