from models.order import Order
from models.conversation import Conversation
from models.contact import Contact


def test_menu_items_returns_expected_tabs(client):
    resp = client.get("/api/menu/items")
    assert resp.status_code == 200
    data = resp.json()
    tab_keys = {t["key"] for t in data["tabs"]}
    assert {"salads", "bowls", "wraps", "byo", "toasties", "smoothies", "drinks"} == tab_keys

    salads_tab = next(t for t in data["tabs"] if t["key"] == "salads")
    assert len(salads_tab["products"]) > 0
    assert salads_tab["addons"]["warm"], "Debe traer proteínas calientes (Premiums) para ensaladas"
    assert salads_tab["addons"]["cold"], "Debe traer adicionales fríos (Premiums) para ensaladas"

    caesar = next(p for p in salads_tab["products"] if "Sassy Caesar" in p["title"])
    assert caesar["has_sizes"] is True
    assert {s["code"] for s in caesar["sizes"]} == {"regular", "large"}


def test_public_order_recalculates_price_and_ignores_client_total(client, clayton_branch, db_session):
    payload = {
        "branch_code": "CLY",
        "delivery_type": "delivery",
        "delivery_address": "Av. Paseo del Mar, PH Mystic",
        "payment_method": "yappy",
        "customer_name": "Cliente de Prueba",
        "customer_phone": "6552-3134",
        "items": [
            {"sku": "SAL_CAESAR_LRG", "quantity": 1, "addon_skus": ["PRM_POLLO_SPICED"], "notes": "sin cebolla"},
            {"sku": "SMO_SUPERNOVA", "quantity": 1, "addon_skus": []},
        ],
    }
    resp = client.post(
        "/api/orders/public",
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 13.95 (large) + 4.00 (pollo spiced) + 9.00 (smoothie) = 26.95 ; delivery +3.50 = 30.45
    assert data["subtotal"] == "26.95"
    assert data["delivery_cost"] == "3.50"
    assert data["total"] == "30.45"
    assert data["whatsapp_url"].startswith("https://wa.me/")
    assert data["order_code"].startswith("FH-")

    order = db_session.query(Order).filter(Order.order_code == data["order_code"]).first()
    assert order is not None
    assert str(order.total) == "30.45"
    assert order.branch_id == clayton_branch.id

    conv = db_session.query(Conversation).filter(Conversation.id == order.conversation_id).first()
    assert conv.delivery_type == "delivery"
    assert conv.payment_method == "yappy"
    assert conv.branch_id == clayton_branch.id

    contact = db_session.query(Contact).filter(Contact.id == conv.customer_id).first()
    assert contact.phone == "+65523134"


def test_public_order_rejects_unknown_sku(client, clayton_branch):
    payload = {
        "branch_code": "CLY",
        "delivery_type": "pickup",
        "payment_method": "cash",
        "customer_name": "Cliente",
        "customer_phone": "6000-0000",
        "items": [{"sku": "SKU_QUE_NO_EXISTE", "quantity": 1, "addon_skus": []}],
    }
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 400


def test_public_order_requires_delivery_address_when_delivery(client, clayton_branch):
    payload = {
        "branch_code": "CLY",
        "delivery_type": "delivery",
        "payment_method": "cash",
        "customer_name": "Cliente",
        "customer_phone": "6000-0000",
        "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
    }
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 400


def test_public_order_rejects_invalid_branch(client):
    payload = {
        "branch_code": "NOPE",
        "delivery_type": "pickup",
        "payment_method": "cash",
        "customer_name": "Cliente",
        "customer_phone": "6000-0000",
        "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
    }
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 400
