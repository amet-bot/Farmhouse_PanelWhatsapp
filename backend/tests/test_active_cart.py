from datetime import datetime, timedelta, timezone

from conftest import auth_headers_for
from models.order import Order
from models.conversation import Conversation
from models.contact import Contact
from security.auth import create_menu_session_token


def _make_conversation(db_session, clayton_branch):
    contact = Contact(name="Amet", phone="+50765523134", created_at=datetime.now(timezone.utc), last_interaction=datetime.now(timezone.utc))
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    db_session.refresh(contact)
    return conv, contact


def test_cart_sync_requires_valid_session(client, clayton_branch):
    resp = client.put(
        "/api/orders/cart",
        json={"session": "token-invalido-o-falsificado", "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 401


def test_cart_sync_creates_single_active_cart_row_and_updates_in_place(client, clayton_branch, db_session):
    conv, _ = _make_conversation(db_session, clayton_branch)
    session_token = create_menu_session_token(conv.id, clayton_branch.id)

    payload = {
        "session": session_token,
        "branch_code": "CLY",
        "delivery_type": "pickup",
        "items": [{"sku": "SAL_CAESAR_LRG", "quantity": 1, "addon_skus": ["PRM_POLLO_SPICED"]}],
    }
    resp = client.put("/api/orders/cart", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["subtotal"] == "17.95"  # 13.95 (large) + 4.00 (pollo spiced)
    assert data["delivery_fee"] == "0.00"
    assert data["total"] == "17.95"
    assert data["status"] == "carrito_activo"

    carts = db_session.query(Order).filter(Order.conversation_id == conv.id).all()
    assert len(carts) == 1
    assert carts[0].status == "carrito_activo"
    assert str(carts[0].subtotal) == "17.95"

    # Agregar un segundo producto: DEBE actualizar la misma fila, nunca crear una segunda (Punto 12).
    payload["items"].append({"sku": "SMO_SUPERNOVA", "quantity": 1, "addon_skus": []})
    resp2 = client.put("/api/orders/cart", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["subtotal"] == "26.95"

    carts_after = db_session.query(Order).filter(Order.conversation_id == conv.id).all()
    assert len(carts_after) == 1
    assert carts_after[0].id == carts[0].id


def test_cart_sync_switching_to_delivery_captures_address_without_auto_surcharge(client, clayton_branch, db_session):
    conv, _ = _make_conversation(db_session, clayton_branch)
    session_token = create_menu_session_token(conv.id, clayton_branch.id)
    payload = {
        "session": session_token,
        "delivery_type": "delivery",
        "delivery_address": "Calle 50",
        "items": [{"sku": "DRK_AGUA", "quantity": 2, "addon_skus": []}],
    }
    resp = client.put("/api/orders/cart", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["delivery_fee"] == "0.00"
    assert float(data["total"]) == round(float(data["subtotal"]), 2)


def test_cart_sync_empty_items_clears_draft(client, clayton_branch, db_session):
    conv, _ = _make_conversation(db_session, clayton_branch)
    session_token = create_menu_session_token(conv.id, clayton_branch.id)
    payload = {"session": session_token, "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}]}
    client.put("/api/orders/cart", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert db_session.query(Order).filter(Order.conversation_id == conv.id).count() == 1

    resp = client.put("/api/orders/cart", json={"session": session_token, "items": []}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "empty"
    assert db_session.query(Order).filter(Order.conversation_id == conv.id).count() == 0


def test_checkout_confirms_the_same_cart_row_instead_of_creating_a_new_order(client, clayton_branch, db_session):
    conv, contact = _make_conversation(db_session, clayton_branch)
    session_token = create_menu_session_token(conv.id, clayton_branch.id)

    cart_payload = {"session": session_token, "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}]}
    client.put("/api/orders/cart", json=cart_payload, headers={"X-Requested-With": "XMLHttpRequest"})
    draft = db_session.query(Order).filter(Order.conversation_id == conv.id).first()
    assert draft.status == "carrito_activo"
    draft_id = draft.id

    checkout_payload = {
        "branch_code": "CLY",
        "delivery_type": "pickup",
        "payment_method": "cash",
        "customer_name": "Amet",
        "customer_phone": "50765523134",
        "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
        "session": session_token,
    }
    resp = client.post("/api/orders/public", json=checkout_payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conversation_id"] == conv.id
    assert data["order_code"].startswith("FH-")

    orders = db_session.query(Order).filter(Order.conversation_id == conv.id).all()
    assert len(orders) == 1, "El checkout debe confirmar el carrito activo, no crear un pedido duplicado."
    assert orders[0].id == draft_id
    assert orders[0].status == "en_proceso"
    assert orders[0].order_code == data["order_code"]


def test_active_cart_expires_after_inactivity(client, clayton_branch, db_session, admin_user):
    conv, _ = _make_conversation(db_session, clayton_branch)
    session_token = create_menu_session_token(conv.id, clayton_branch.id)
    payload = {"session": session_token, "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}]}
    client.put("/api/orders/cart", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})

    cart = db_session.query(Order).filter(Order.conversation_id == conv.id).first()
    cart.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    resp = client.get(f"/api/conversations/{conv.id}", headers=auth_headers_for(admin_user))
    assert resp.status_code == 200, resp.text
    orders = resp.json()["orders"]
    assert orders[0]["status"] == "abandonado"
