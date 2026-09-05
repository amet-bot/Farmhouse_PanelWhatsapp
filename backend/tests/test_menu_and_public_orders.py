from config import settings
from models.order import Order
from models.conversation import Conversation
from models.contact import Contact

BASE_ORDER_PAYLOAD = {
    "branch_code": "CLY",
    "delivery_type": "pickup",
    "payment_method": "cash",
    "customer_name": "Cliente WhatsApp",
    "customer_phone": "6000-1111",
    "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
}


def test_menu_items_returns_expected_tabs(client):
    resp = client.get("/api/menu/items")
    assert resp.status_code == 200
    data = resp.json()
    tab_keys = {t["key"] for t in data["tabs"]}
    assert {"salads", "bowls", "wraps", "byo", "toasties", "smoothies", "drinks", "vitrina", "merch"} == tab_keys

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

    # 13.95 (large) + 4.00 (pollo spiced) + 9.00 (smoothie) = 26.95 ; delivery a coordinar (0.00 en menú) = 26.95
    assert data["subtotal"] == "26.95"
    assert data["delivery_cost"] == "0.00"
    assert data["total"] == "26.95"
    assert data["whatsapp_url"].startswith("https://wa.me/")
    assert data["order_code"].startswith("FH-")

    order = db_session.query(Order).filter(Order.order_code == data["order_code"]).first()
    assert order is not None
    assert str(order.total) == "26.95"
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


def test_whatsapp_url_always_targets_official_number(client, clayton_branch):
    """El enlace wa.me nunca debe generarse sin número: eso hace que WhatsApp muestre su
    selector de chats en vez de abrir la conversación de Farmhouse directamente."""
    resp = client.post("/api/orders/public", json=BASE_ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    whatsapp_url = resp.json()["whatsapp_url"]
    # El número debe aparecer inmediatamente después de wa.me/, sin quedar vacío.
    assert whatsapp_url.startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")


def test_public_order_ignores_tampered_origin_wa(client, clayton_branch):
    """Un ?wa= que no coincide con el número oficial (ej. alguien editando la URL del menú)
    debe ignorarse por completo: el pedido siempre debe ir al número oficial de Farmhouse."""
    payload = {**BASE_ORDER_PAYLOAD, "origin_wa": "50799999999"}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    whatsapp_url = resp.json()["whatsapp_url"]
    assert whatsapp_url.startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")
    assert "50799999999" not in whatsapp_url


def test_public_order_accepts_matching_origin_wa(client, clayton_branch):
    """Un ?wa= que sí coincide con el número oficial (el caso real hoy, ya que Farmhouse usa
    una sola línea de WhatsApp) se acepta sin cambiar el resultado."""
    payload = {**BASE_ORDER_PAYLOAD, "origin_wa": settings.META_WA_DISPLAY_NUMBER}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")


def test_obarrio_without_wa_param_still_resolves_a_number(client, obarrio_branch):
    """Caso reportado por el usuario: entrar directo a /menu (sin ?wa=), seleccionar Obarrio y
    enviar el pedido. No debe requerirse ?wa= para nada — la sucursal por sí sola debe bastar
    para resolver un número de WhatsApp válido."""
    payload = {**BASE_ORDER_PAYLOAD, "branch_code": "OBR"}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")


def test_branch_specific_whatsapp_override_takes_priority(client, obarrio_branch, monkeypatch):
    """Si en el futuro se configura un número propio para una sucursal (BRANCH_WHATSAPP_NUMBERS),
    ese número debe usarse en vez del general — sin necesidad de ningún ?wa=."""
    monkeypatch.setattr(settings, "BRANCH_WHATSAPP_NUMBERS", '{"OBR": "+507 6111-2222"}')
    payload = {**BASE_ORDER_PAYLOAD, "branch_code": "OBR"}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith("https://wa.me/50761112222?text=")


def test_branch_override_missing_falls_back_to_official(client, obarrio_branch, monkeypatch):
    """Un mapa de overrides configurado, pero sin entrada para la sucursal seleccionada, debe
    caer al número oficial general sin fallar."""
    monkeypatch.setattr(settings, "BRANCH_WHATSAPP_NUMBERS", '{"CLY": "50763334444"}')
    payload = {**BASE_ORDER_PAYLOAD, "branch_code": "OBR"}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")


def test_malformed_branch_overrides_are_ignored_not_fatal(client, obarrio_branch, monkeypatch):
    """BRANCH_WHATSAPP_NUMBERS con JSON inválido nunca debe tumbar el endpoint: se ignora por
    completo y se usa el número oficial general."""
    monkeypatch.setattr(settings, "BRANCH_WHATSAPP_NUMBERS", "esto no es JSON valido {{{")
    payload = {**BASE_ORDER_PAYLOAD, "branch_code": "OBR"}
    resp = client.post("/api/orders/public", json=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith(f"https://wa.me/{settings.META_WA_DISPLAY_NUMBER}?text=")


def test_public_order_fails_loudly_without_official_number(client, clayton_branch, db_session, monkeypatch):
    """Si META_WA_DISPLAY_NUMBER no está configurado (ej. falta en las variables de Railway),
    el endpoint debe fallar con un 500 explícito ANTES de crear nada en la base de datos —
    nunca debe devolver un wa.me/?text= sin número."""
    monkeypatch.setattr(settings, "META_WA_DISPLAY_NUMBER", None)
    orders_before = db_session.query(Order).count()

    resp = client.post("/api/orders/public", json=BASE_ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"})

    assert resp.status_code == 500
    assert db_session.query(Order).count() == orders_before


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
