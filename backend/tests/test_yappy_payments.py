import base64
import hashlib
import hmac

from config import settings
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
    assert "Pagar%20con%20Yappy" in data["whatsapp_url"]
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
