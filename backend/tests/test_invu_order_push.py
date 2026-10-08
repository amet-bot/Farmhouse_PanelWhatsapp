"""Envío de pedidos del Menú Digital a la pantalla de Invu (services/invu_order_push.py)."""
import json
from decimal import Decimal

import pytest

from config import settings
from models.contact import Contact
from models.conversation import Conversation
from models.order import Order
from services import invu_client, invu_order_push


class _NonClosing:
    """La sesión de las pruebas se comparte: el servicio la cierra al terminar, aquí no."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


@pytest.fixture
def order(db_session, clayton_branch):
    contact = Contact(name="Ana Prueba", phone="+50761234567")
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, status="open", branch_id=clayton_branch.id)
    db_session.add(conv)
    db_session.flush()
    o = Order(
        order_code="FH-TEST01", conversation_id=conv.id, branch_id=clayton_branch.id,
        order_type="takeout", status="en_proceso", subtotal=Decimal("10.00"),
        delivery_cost=Decimal("0.00"), total=Decimal("10.00"),
        items_json=json.dumps({
            "source": "menu_web",
            "payment_method": "cash",
            "items": [{"sku": "BOWL1", "title": "Bowl", "quantity": 2, "unit_price": 5.0, "addons": [], "notes": None}],
        }),
    )
    db_session.add(o)
    db_session.commit()
    return o


@pytest.fixture
def encendido(monkeypatch, db_session):
    monkeypatch.setattr(settings, "INVU_ORDER_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "INVU_USER_CLY", "u")
    monkeypatch.setattr(settings, "INVU_PASS_CLY", "p")
    monkeypatch.setattr(invu_order_push, "SessionLocal", lambda: _NonClosing(db_session))


def _estado(db_session, order):
    db_session.refresh(order)
    return json.loads(order.items_json).get("invu_push")


def test_apagado_no_manda_nada(monkeypatch, order):
    llamadas = []
    monkeypatch.setattr(invu_client, "create_order", lambda *a: llamadas.append(a))
    assert settings.INVU_ORDER_PUSH_ENABLED is False
    assert invu_order_push.push_order_to_invu(order.id) is None
    assert llamadas == []


def test_envia_y_queda_registrado(encendido, monkeypatch, db_session, order):
    enviados = []
    monkeypatch.setattr(invu_client, "create_order", lambda cred, body: enviados.append((cred, body)) or {"ok": 1})
    assert invu_order_push.push_order_to_invu(order.id) == "sent"
    cred, body = enviados[0]
    assert cred.username == "u"
    assert body["reference"] == "FH-TEST01"
    assert body["customer"]["phone"] == "+50761234567"
    assert body["items"][0]["quantity"] == 2
    assert _estado(db_session, order)["status"] == "sent"


def test_no_reenvia_una_orden_ya_enviada(encendido, monkeypatch, db_session, order):
    llamadas = []
    monkeypatch.setattr(invu_client, "create_order", lambda *a: llamadas.append(a) or {})
    invu_order_push.push_order_to_invu(order.id)
    assert invu_order_push.push_order_to_invu(order.id) is None
    assert len(llamadas) == 1


def test_fallo_de_invu_no_lanza_y_queda_anotado(encendido, monkeypatch, db_session, order):
    def falla(*a):
        raise invu_client.InvuError("endpoint no habilitado")

    monkeypatch.setattr(invu_client, "create_order", falla)
    assert invu_order_push.push_order_to_invu(order.id) == "failed"
    estado = _estado(db_session, order)
    assert estado["status"] == "failed" and "no habilitado" in estado["detail"]


def test_un_fallo_se_puede_reintentar(encendido, monkeypatch, db_session, order):
    monkeypatch.setattr(invu_client, "create_order", lambda *a: (_ for _ in ()).throw(invu_client.InvuError("x")))
    invu_order_push.push_order_to_invu(order.id)
    monkeypatch.setattr(invu_client, "create_order", lambda *a: {})
    assert invu_order_push.push_order_to_invu(order.id) == "sent"


def test_sucursal_sin_usuario_de_api_se_omite(encendido, monkeypatch, db_session, order):
    monkeypatch.setattr(settings, "INVU_USER_CLY", None)
    monkeypatch.setattr(invu_client, "create_order", lambda *a: pytest.fail("no debía llamarse"))
    assert invu_order_push.push_order_to_invu(order.id) is None
    assert _estado(db_session, order)["status"] == "skipped"
