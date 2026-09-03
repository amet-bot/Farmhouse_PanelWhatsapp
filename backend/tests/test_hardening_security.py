import hmac
import hashlib
import json
from decimal import Decimal
import pytest
from config import settings
from models.conversation import Conversation
from models.contact import Contact
from models.message import Message
from models.order import Order
from tests.conftest import auth_headers_for

# 1. Login con usuario inactivo (Punto 9)
def test_login_inactive_user_returns_403(client, inactive_user):
    resp = client.post("/api/auth/login", json={
        "username": inactive_user.username,
        "password": "Inactive123!"
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403
    assert "desactivada" in resp.json()["detail"].lower() or "inactiva" in resp.json()["detail"].lower()

# 2. Validación de contraseñas >= 4 caracteres
def test_create_user_short_password_rejected(client, admin_user):
    headers = auth_headers_for(admin_user)
    resp = client.post("/api/users/", headers=headers, json={
        "username": "nuevo_usuario",
        "name": "Nuevo Usuario",
        "email": "nuevo@farmhouse.pa",
        "password": "12", # < 4 caracteres
        "role": "agent",
        "branch_id": 1
    })
    assert resp.status_code == 422

# 3. Webhook de Meta: Firma HMAC-SHA256 obligatoria y validación (Punto 2)
def test_webhook_meta_signature_required(client, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "meta")
    monkeypatch.setattr(settings, "META_APP_SECRET", "test_secret_123456789")

    payload = json.dumps({"entry": []}).encode("utf-8")

    # Sin firma -> 403
    resp_no_sig = client.post("/api/webhooks/whatsapp", content=payload, headers={"Content-Type": "application/json"})
    assert resp_no_sig.status_code == 403

    # Con firma inválida -> 403
    resp_bad_sig = client.post(
        "/api/webhooks/whatsapp",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalidhash"}
    )
    assert resp_bad_sig.status_code == 403

    # Con firma válida -> 200 (ignored o procesado)
    valid_hash = hmac.new(b"test_secret_123456789", payload, hashlib.sha256).hexdigest()
    resp_valid = client.post(
        "/api/webhooks/whatsapp",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={valid_hash}"}
    )
    assert resp_valid.status_code == 200

# 4. Idempotencia en Webhooks por wamid (Punto 4)
def test_webhook_idempotency_duplicate_wamid(client, clayton_branch, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "50761112222",
                        "id": "wamid.HBgLMzgxMjM0NTY3OAA=",
                        "timestamp": "1725000000",
                        "text": {"body": "Hola buenas tardes"},
                        "type": "text"
                    }],
                    "contacts": [{"profile": {"name": "Juan Perez"}}]
                }
            }]
        }]
    }

    # Primer envío -> Procesado exitosamente
    resp1 = client.post("/api/webhooks/whatsapp", json=payload)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "received"

    # Segundo envío idéntico -> Detecta duplicado y retorna duplicate
    resp2 = client.post("/api/webhooks/whatsapp", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"

# 5. Aislamiento de conversaciones por sucursal para agentes (Punto 3)
def test_agent_cannot_access_other_branch_conversation(
    client, clayton_agent, obarrio_agent, clayton_branch, obarrio_branch, clayton_device, obarrio_device, db_session
):
    contact = Contact(name="Cliente Obarrio", phone="+50760000001")
    db_session.add(contact)
    db_session.commit()

    conv_obarrio = Conversation(
        customer_id=contact.id,
        branch_id=obarrio_branch.id,
        status="open"
    )
    db_session.add(conv_obarrio)
    db_session.commit()

    # Agente de Clayton intentando ver la conversación de Obarrio -> 403 Forbidden
    headers_clayton = auth_headers_for(clayton_agent, device_id=clayton_device.device_id)
    resp = client.get(f"/api/conversations/{conv_obarrio.id}", headers=headers_clayton)
    assert resp.status_code == 403

    # Agente de Obarrio viendo su propia conversación -> 200 OK
    headers_obarrio = auth_headers_for(obarrio_agent, device_id=obarrio_device.device_id)
    resp_ok = client.get(f"/api/conversations/{conv_obarrio.id}", headers=headers_obarrio)
    assert resp_ok.status_code == 200

# 6. Aislamiento de contactos por sucursal (Punto 7)
def test_agent_contacts_isolated_by_branch(
    client, clayton_agent, clayton_branch, obarrio_branch, clayton_device, db_session
):
    c1 = Contact(name="Cliente Clayton", phone="+50760000010")
    c2 = Contact(name="Cliente Obarrio", phone="+50760000020")
    db_session.add_all([c1, c2])
    db_session.commit()

    conv1 = Conversation(customer_id=c1.id, branch_id=clayton_branch.id, status="open")
    conv2 = Conversation(customer_id=c2.id, branch_id=obarrio_branch.id, status="open")
    db_session.add_all([conv1, conv2])
    db_session.commit()

    headers = auth_headers_for(clayton_agent, device_id=clayton_device.device_id)
    resp = client.get("/api/contacts/", headers=headers)
    assert resp.status_code == 200
    contact_ids = [c["id"] for c in resp.json()]
    assert c1.id in contact_ids
    assert c2.id not in contact_ids

# 7. Comandas con cálculo decimal e ITBMS 7% (Punto 8)
def test_create_order_decimal_calculations_and_tax(client, admin_user, clayton_branch, db_session):
    contact = Contact(name="Cliente Pedido", phone="+50769999999")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open")
    db_session.add(conv)
    db_session.commit()

    headers = auth_headers_for(admin_user)
    # subtotal = 20.00, delivery = 3.50 -> tax(7%) = 1.40 -> total = 24.90
    resp = client.post("/api/orders/", headers=headers, json={
        "conversation_id": conv.id,
        "branch_id": clayton_branch.id,
        "order_type": "delivery",
        "subtotal": 20.00,
        "delivery_cost": 3.50,
        "items": [
            {"name": "Burger Especial", "quantity": 2, "price": 10.00}
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(str(data["subtotal"])) == Decimal("20.00")
    assert Decimal(str(data["tax"])) == Decimal("1.40")
    assert Decimal(str(data["delivery_cost"])) == Decimal("3.50")
    assert Decimal(str(data["total"])) == Decimal("24.90")
    assert data["order_code"].startswith("FH-")

# 8. Concurrencia Optimista (409 Conflict) al tomar conversación ya asignada (Punto 20)
def test_concurrency_take_conversation_conflict(
    client, clayton_agent, supervisor_user, clayton_branch, clayton_device, db_session
):
    contact = Contact(name="Cliente Concurrente", phone="+50768888888")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(
        customer_id=contact.id,
        branch_id=clayton_branch.id,
        assigned_user_id=clayton_agent.id, # Ya asignada a clayton_agent
        status="open"
    )
    db_session.add(conv)
    db_session.commit()

    # Supervisor intenta tomar la conversación que ya tiene agente -> 409 Conflict
    headers_sup = auth_headers_for(supervisor_user, device_id=clayton_device.device_id)
    resp = client.post(f"/api/conversations/{conv.id}/take", headers=headers_sup)
    assert resp.status_code == 409
    detail_lower = resp.json()["detail"].lower()
    assert "ya fue tomada" in detail_lower or "ya está siendo atendida" in detail_lower

# 9. Borrado lógico de conversación (Punto 21)
def test_soft_delete_conversation(client, supervisor_user, clayton_branch, clayton_device, db_session):
    contact = Contact(name="Cliente Borrado", phone="+50767777777")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open")
    db_session.add(conv)
    db_session.commit()

    headers = auth_headers_for(supervisor_user, device_id=clayton_device.device_id)
    resp_del = client.delete(f"/api/conversations/{conv.id}", headers=headers)
    assert resp_del.status_code == 200

    # Conversación no debe aparecer en listado activo
    resp_list = client.get("/api/conversations/", headers=headers)
    assert resp_list.status_code == 200
    active_ids = [c["id"] for c in resp_list.json()]
    assert conv.id not in active_ids

# 10. Protección de Medios Autenticados (Punto 6)
def test_media_authenticated_route_unauthorized_rejected(client):
    resp = client.get("/api/media/test_image.jpg")
    assert resp.status_code in [401, 403]

# 11. Agente no puede crear comanda para otra sucursal (Punto 8)
def test_agent_cannot_create_order_for_other_branch(
    client, clayton_agent, clayton_branch, obarrio_branch, clayton_device, db_session
):
    contact = Contact(name="Cliente Test", phone="+50761234567")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open")
    db_session.add(conv)
    db_session.commit()

    headers = auth_headers_for(clayton_agent, device_id=clayton_device.device_id)
    # Intenta crear pedido especificando la sucursal de Obarrio
    resp = client.post("/api/orders/", headers=headers, json={
        "conversation_id": conv.id,
        "branch_id": obarrio_branch.id,
        "order_type": "delivery",
        "subtotal": 15.00,
        "delivery_cost": 2.00
    })
    assert resp.status_code == 403
    assert "no tienes permiso" in resp.json()["detail"].lower()

# 12. Generación y consumo atómico de Ticket WebSocket de un solo uso (Punto 14)
def test_ws_ticket_single_use_consumption(client, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, device_id=clayton_device.device_id)
    resp = client.post("/api/auth/ws-token", headers=headers)
    assert resp.status_code == 200
    ticket = resp.json().get("ws_ticket")
    assert ticket is not None

    # Consumir ticket
    from routers.auth import consume_ws_ticket
    user_id = consume_ws_ticket(ticket)
    assert user_id == clayton_agent.id

    # Segundo intento con el mismo ticket debe ser None (de un solo uso)
    second_attempt = consume_ws_ticket(ticket)
    assert second_attempt is None

# 13. Pausa y reanudación de automatización de bot por conversación (Punto 17)
def test_toggle_conversation_automation(client, clayton_agent, clayton_branch, clayton_device, db_session):
    contact = Contact(name="Cliente Auto", phone="+50762223333")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open", automation_paused=False)
    db_session.add(conv)
    db_session.commit()

    headers = auth_headers_for(clayton_agent, device_id=clayton_device.device_id)
    # Pausar
    resp1 = client.post(f"/api/conversations/{conv.id}/toggle-automation", headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["automation_paused"] is True

    # Reanudar
    resp2 = client.post(f"/api/conversations/{conv.id}/toggle-automation", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["automation_paused"] is False

