import base64
import hashlib
import hmac

from config import settings
from conftest import TestingSessionLocal
from models.message import Message
from models.order import Order


ORDER_PAYLOAD = {
    "branch_code": "CLY",
    "delivery_type": "pickup",
    "payment_method": "yappy",
    "customer_name": "Cliente Yappy",
    "customer_phone": "6555-0101",
    "items": [{"sku": "DRK_AGUA", "quantity": 1, "addon_skus": []}],
}


def _enable_yappy(monkeypatch):
    secret = base64.b64encode(b"firma-segura.otro-valor").decode()
    monkeypatch.setattr(settings, "YAPPY_ENABLED", True)
    monkeypatch.setattr(settings, "YAPPY_MERCHANT_ID", "merchant-test")
    monkeypatch.setattr(settings, "YAPPY_SECRET_KEY", secret)
    monkeypatch.setattr(settings, "YAPPY_DOMAIN", "https://farmhouse.example")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://farmhouse.example")
    # El botón real espera unos segundos antes de mandarse (ver YAPPY_BUTTON_SEND_DELAY_SECONDS
    # en routers/orders.py) — en los tests no hace falta esperar de verdad. La tarea en segundo
    # plano abre su propia sesión de DB (SessionLocal), así que también hay que apuntarla a la
    # base de datos de prueba o escribiría en la real.
    monkeypatch.setattr("routers.orders.YAPPY_BUTTON_SEND_DELAY_SECONDS", 0)
    monkeypatch.setattr("routers.orders.SessionLocal", TestingSessionLocal)
    return secret


def test_yappy_order_sends_signed_payment_link_to_whatsapp(
    client, clayton_branch, db_session, monkeypatch
):
    _enable_yappy(monkeypatch)
    response = client.post(
        "/api/orders/public",
        json=ORDER_PAYLOAD,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["payment_url"].startswith(
        "https://farmhouse.example/pago-yappy?order=FH-"
    )
    # El link NO va como texto plano en el mensaje que el cliente confirma (whatsapp_url):
    # ese mismo enlace ya llega como un botón real y tocable en un mensaje aparte (abajo),
    # repetirlo como URL suelta en el resumen del pedido sería redundante y se ve mal.
    assert "pago-yappy" not in data["whatsapp_url"]
    assert "Pagar%20con%20Yappy" not in data["whatsapp_url"]
    message = (
        db_session.query(Message)
        .filter(Message.conversation_id == data["conversation_id"])
        .one()
    )
    assert data["payment_url"] in message.content
    assert message.status == "sent"

    query = data["payment_url"].split("?", 1)[1]
    token = query.split("token=", 1)[1]
    api_response = client.get(
        f"/api/payments/yappy/orders/{data['order_code']}?token={token}"
    )
    assert api_response.status_code == 200
    assert api_response.json()["total"] == "2.00"


def test_yappy_session_and_signed_ipn_update_payment_status(
    client, clayton_branch, db_session, monkeypatch
):
    secret = _enable_yappy(monkeypatch)
    created = client.post(
        "/api/orders/public",
        json=ORDER_PAYLOAD,
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).json()
    token = created["payment_url"].split("token=", 1)[1]

    async def fake_create_yappy_order(**kwargs):
        assert kwargs["phone"] == "+65550101"
        assert str(kwargs["total"]) == "2.00"
        return {
            "transactionId": "TX-123",
            "token": "payment-token",
            "documentName": "document",
        }

    monkeypatch.setattr("routers.payments.create_yappy_order", fake_create_yappy_order)
    session_response = client.post(
        "/api/payments/yappy/session",
        json={"order_code": created["order_code"], "token": token},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert session_response.status_code == 200
    order = (
        db_session.query(Order).filter(Order.order_code == created["order_code"]).one()
    )
    assert order.payment_status == "awaiting_customer"
    assert order.payment_reference == "TX-123"

    signing_secret = base64.b64decode(secret).decode().split(".", 1)[0]
    domain = "https://farmhouse.example"
    signature = hmac.new(
        signing_secret.encode(), f"{order.order_code}E{domain}".encode(), hashlib.sha256
    ).hexdigest()
    ipn = client.get(
        "/api/payments/yappy/ipn",
        params={
            "orderId": order.order_code,
            "status": "E",
            "Hash": signature,
            "domain": domain,
            "confirmationNumber": "CONF-999",
        },
    )
    assert ipn.status_code == 200, ipn.text
    db_session.refresh(order)
    assert order.payment_status == "paid"
    assert order.payment_confirmation_number == "CONF-999"

    # Una notificación posterior de otro intento (ej. expirado) no debe degradar un pedido
    # que ya quedó pagado.
    expired_signature = hmac.new(
        signing_secret.encode(), f"{order.order_code}X{domain}".encode(), hashlib.sha256
    ).hexdigest()
    late_ipn = client.get(
        "/api/payments/yappy/ipn",
        params={
            "orderId": order.order_code,
            "status": "X",
            "Hash": expired_signature,
            "domain": domain,
        },
    )
    assert late_ipn.status_code == 200, late_ipn.text
    db_session.refresh(order)
    assert order.payment_status == "paid"


def _pay_order_via_signed_ipn(client, monkeypatch, secret, order_code, *, status_code="E", confirmation_number=None):
    signing_secret = base64.b64decode(secret).decode().split(".", 1)[0]
    domain = "https://farmhouse.example"
    signature = hmac.new(
        signing_secret.encode(), f"{order_code}{status_code}{domain}".encode(), hashlib.sha256
    ).hexdigest()
    params = {"orderId": order_code, "status": status_code, "Hash": signature, "domain": domain}
    if confirmation_number:
        params["confirmationNumber"] = confirmation_number
    return client.get("/api/payments/yappy/ipn", params=params)


def test_ipn_paid_notifies_the_customer_by_whatsapp(client, clayton_branch, db_session, monkeypatch):
    """Antes de esto, el IPN solo actualizaba el estado interno y avisaba al panel — el
    cliente se quedaba sin saber que su pago ya se completó."""
    secret = _enable_yappy(monkeypatch)
    created = client.post(
        "/api/orders/public", json=ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"},
    ).json()
    token = created["payment_url"].split("token=", 1)[1]

    async def fake_create_yappy_order(**kwargs):
        return {"transactionId": "TX-456", "token": "payment-token", "documentName": "document"}

    monkeypatch.setattr("routers.payments.create_yappy_order", fake_create_yappy_order)
    client.post(
        "/api/payments/yappy/session",
        json={"order_code": created["order_code"], "token": token},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    ipn = _pay_order_via_signed_ipn(client, monkeypatch, secret, created["order_code"], confirmation_number="CONF-1")
    assert ipn.status_code == 200, ipn.text

    messages_to_customer = db_session.query(Message).filter(
        Message.conversation_id == created["conversation_id"], Message.direction == "outgoing",
    ).all()
    success_msgs = [m for m in messages_to_customer if "Pago recibido con éxito" in m.content]
    assert len(success_msgs) == 1
    assert created["order_code"] in success_msgs[0].content


def test_ipn_does_not_notify_twice_for_the_same_payment(client, clayton_branch, db_session, monkeypatch):
    """Yappy puede reintentar la misma notificación (ej. por reliability de su lado) — el
    cliente no debe recibir el mismo 'pago exitoso' más de una vez."""
    secret = _enable_yappy(monkeypatch)
    created = client.post(
        "/api/orders/public", json=ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"},
    ).json()
    token = created["payment_url"].split("token=", 1)[1]

    async def fake_create_yappy_order(**kwargs):
        return {"transactionId": "TX-789", "token": "payment-token", "documentName": "document"}

    monkeypatch.setattr("routers.payments.create_yappy_order", fake_create_yappy_order)
    client.post(
        "/api/payments/yappy/session",
        json={"order_code": created["order_code"], "token": token},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    first = _pay_order_via_signed_ipn(client, monkeypatch, secret, created["order_code"])
    assert first.status_code == 200, first.text
    second = _pay_order_via_signed_ipn(client, monkeypatch, secret, created["order_code"])
    assert second.status_code == 200, second.text

    messages_to_customer = db_session.query(Message).filter(
        Message.conversation_id == created["conversation_id"],
    ).all()
    success_msgs = [m for m in messages_to_customer if "Pago recibido con éxito" in m.content]
    assert len(success_msgs) == 1


def _post_mi_pedido_farmhouse_text(client, phone_digits, wamid):
    """Simula al cliente reenviando el texto pre-armado que /menu le abre en WhatsApp
    (ver routers.orders._build_whatsapp_order_text) — así se dispara _step_confirm_web_menu_order."""
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [
                {"from": phone_digits, "id": wamid, "timestamp": "1725500000", "type": "text",
                 "text": {"body": "*MI PEDIDO FARMHOUSE*\nDelivery"}},
            ]},
            "field": "messages",
        }]}],
    })


def test_confirmation_mentions_the_yappy_button_when_yappy_is_configured(client, clayton_branch, db_session, monkeypatch):
    """Antes de esto, el texto siempre decía 'coordinará el pago contigo' sin importar si ya
    se había mandado un botón real de Yappy — quedaba engañoso una vez Yappy esté activo."""
    _enable_yappy(monkeypatch)
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)

    created = client.post(
        "/api/orders/public", json=ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"},
    ).json()

    resp = _post_mi_pedido_farmhouse_text(client, "65550101", "wamid.confirm.1")
    assert resp.status_code == 200

    reply = db_session.query(Message).filter(
        Message.conversation_id == created["conversation_id"], Message.direction == "outgoing",
    ).order_by(Message.created_at.desc(), Message.id.desc()).first()
    assert "botón para pagar con Yappy" in reply.content
    assert "coordinará el pago contigo" not in reply.content


def test_confirmation_stays_generic_when_yappy_is_not_configured(client, clayton_branch, db_session, monkeypatch):
    """Sin credenciales (el estado real hoy), no se manda ningún botón — el texto no debe
    insinuar que sí se mandó uno."""
    monkeypatch.setattr(settings, "YAPPY_ENABLED", False)
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)

    created = client.post(
        "/api/orders/public", json=ORDER_PAYLOAD, headers={"X-Requested-With": "XMLHttpRequest"},
    ).json()

    resp = _post_mi_pedido_farmhouse_text(client, "65550101", "wamid.confirm.2")
    assert resp.status_code == 200

    reply = db_session.query(Message).filter(
        Message.conversation_id == created["conversation_id"], Message.direction == "outgoing",
    ).order_by(Message.created_at.desc(), Message.id.desc()).first()
    assert "coordinará el pago contigo" in reply.content
    assert "botón para pagar con Yappy" not in reply.content


def test_yappy_ipn_rejected_when_yappy_is_not_configured(
    client, clayton_branch, db_session, monkeypatch
):
    """
    Sin credenciales de Yappy la clave de firma queda vacía y cualquiera podría calcular un
    hash válido (el dominio es público) para marcar un pedido como pagado. El IPN debe
    rechazarse de plano mientras Yappy no esté configurado.
    """
    _enable_yappy(monkeypatch)
    response = client.post(
        "/api/orders/public",
        json=ORDER_PAYLOAD,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200, response.text
    order_code = response.json()["order_code"]

    # Yappy se apaga (estado real hoy: sin credenciales en el entorno).
    monkeypatch.setattr(settings, "YAPPY_ENABLED", False)
    monkeypatch.setattr(settings, "YAPPY_SECRET_KEY", None)

    domain = "https://farmhouse.example"
    forged = hmac.new(b"", f"{order_code}E{domain}".encode(), hashlib.sha256).hexdigest()
    ipn = client.get(
        "/api/payments/yappy/ipn",
        params={"orderId": order_code, "status": "E", "Hash": forged, "domain": domain},
    )
    assert ipn.status_code == 503, ipn.text

    order = db_session.query(Order).filter(Order.order_code == order_code).one()
    assert order.payment_status != "paid"
