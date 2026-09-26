"""
Regresiones de la auditoría de estabilización (bloque 2: datos de inventario, prep, pedidos,
Invu y bot).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import auth_headers_for
from tests.test_transfers_inventory import _create_item, _stock_shipment
from tests.test_prep import _create_template
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.user import User


# ---- Traslados en la existencia ----

def _full_transfer(client, headers_from, headers_to, from_id, to_id, item_id, qty, receive=True):
    t = client.post("/api/transfers/", json={
        "from_branch_id": from_id, "to_branch_id": to_id,
        "items": [{"inventory_item_id": item_id, "quantity": qty}],
    }, headers=headers_from).json()
    assert client.post(f"/api/transfers/{t['id']}/approve", json={}, headers=headers_from).status_code == 200
    assert client.post(f"/api/transfers/{t['id']}/dispatch", headers=headers_from).status_code == 200
    if receive:
        assert client.post(f"/api/transfers/{t['id']}/receive", headers=headers_to).status_code == 200
    return t


def _stock_row(client, headers, branch_id, item_id):
    rows = client.get(f"/api/inventory/stock?branch_id={branch_id}", headers=headers).json()
    return next(r for r in rows if r["inventory_item_id"] == item_id)


def test_stock_reflects_transfers(client, clayton_branch, obarrio_branch, clayton_agent, clayton_device,
                                  obarrio_agent, obarrio_device):
    h_cly = auth_headers_for(clayton_agent, clayton_device.device_id)
    h_obr = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    item = _create_item(client, h_cly, "Arroz")
    _stock_shipment(client, h_cly, clayton_branch.id, item["id"], "20")
    _full_transfer(client, h_cly, h_obr, clayton_branch.id, obarrio_branch.id, item["id"], "5")

    cly = _stock_row(client, h_cly, clayton_branch.id, item["id"])
    obr = _stock_row(client, h_obr, obarrio_branch.id, item["id"])
    assert Decimal(cly["on_hand"]) == Decimal("15")
    assert Decimal(cly["transferred"]) == Decimal("-5")
    assert Decimal(obr["on_hand"]) == Decimal("5")


def test_count_after_transfer_does_not_double_count_in_ledger(client, clayton_branch, obarrio_branch,
                                                             clayton_agent, clayton_device,
                                                             obarrio_agent, obarrio_device):
    h_cly = auth_headers_for(clayton_agent, clayton_device.device_id)
    h_obr = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    item = _create_item(client, h_cly, "Frijol")
    _stock_shipment(client, h_cly, clayton_branch.id, item["id"], "20")
    _full_transfer(client, h_cly, h_obr, clayton_branch.id, obarrio_branch.id, item["id"], "5")

    # Obarrio cuenta lo que llegó: el esperado ya es 5, así que no hay diferencia que ajustar.
    res = client.post("/api/inventory/counts", json={
        "branch_id": obarrio_branch.id,
        "items": [{"inventory_item_id": item["id"], "counted_quantity": "5"}],
    }, headers=h_obr)
    assert res.status_code == 201, res.text
    line = res.json()["items"][0]
    assert Decimal(line["expected_quantity"]) == Decimal("5")
    assert Decimal(line["difference"]) == Decimal("0")

    cmp_rows = client.get(f"/api/inventory/movements/compare?branch_id={obarrio_branch.id}", headers=h_obr).json()
    row = next(r for r in cmp_rows if r["inventory_item_id"] == item["id"])
    assert Decimal(row["on_hand_movements"]) == Decimal("5")


def test_in_transit_counts_as_out_of_origin_only(client, clayton_branch, obarrio_branch, clayton_agent,
                                                 clayton_device, obarrio_agent, obarrio_device):
    h_cly = auth_headers_for(clayton_agent, clayton_device.device_id)
    h_obr = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    item = _create_item(client, h_cly, "Lenteja")
    _stock_shipment(client, h_cly, clayton_branch.id, item["id"], "10")
    _full_transfer(client, h_cly, h_obr, clayton_branch.id, obarrio_branch.id, item["id"], "4", receive=False)
    assert Decimal(_stock_row(client, h_cly, clayton_branch.id, item["id"])["on_hand"]) == Decimal("6")
    rows_obr = client.get(f"/api/inventory/stock?branch_id={obarrio_branch.id}", headers=h_obr).json()
    obr = next(r for r in rows_obr if r["inventory_item_id"] == item["id"])
    assert Decimal(obr["on_hand"]) == Decimal("0")


def test_agent_without_branch_gets_403_not_all_branches(client, db_session, clayton_branch, admin_user):
    agent = User(id=30, username="sin_sucursal", name="Sin Sucursal", password_hash="x", role="agent",
                 branch_id=None, active=True)
    db_session.add(agent)
    db_session.commit()
    # Sin dispositivo el agente no pasa la autorización; se simula uno de su (inexistente)
    # sucursal no es posible, así que se prueba la función directamente.
    from routers.inventory import _visible_branch_filter
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _visible_branch_filter(agent, None)
    assert exc.value.status_code == 403


# ---- Prep ----

def _with_foreign_keys(db_session):
    db_session.execute(text("PRAGMA foreign_keys=ON"))


def test_prep_template_editable_after_checks_and_keeps_history(client, db_session, clayton_branch,
                                                               supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)
    kale = next(i for i in template["items"] if i["name"] == "Kale picado")
    tomates = next(i for i in template["items"] if i["name"] == "Tomates cherry")
    res = client.post(f"/api/prep/templates/{template['id']}/checks", json={
        "checkpoint": "10am",
        "entries": [{"template_item_id": kale["id"], "on_hand": "2"}, {"template_item_id": tomates["id"], "on_hand": "1"}],
    }, headers=headers)
    assert res.status_code == 201

    _with_foreign_keys(db_session)
    try:
        # Se edita el par del kale, se saca tomates (que ya tiene historial) y se agrega uno nuevo.
        res = client.put(f"/api/prep/templates/{template['id']}", json={
            "branch_id": clayton_branch.id, "name": "Bowls", "checkpoints": ["10am", "3pm"],
            "items": [
                {"id": kale["id"], "section": "Base", "name": "Kale picado", "unit_label": "cambro L", "par_target": "4"},
                {"section": "Base", "name": "Quinoa", "unit_label": "1/1", "par_target": "1"},
            ],
        }, headers=headers)
        assert res.status_code == 200, res.text
    finally:
        db_session.execute(text("PRAGMA foreign_keys=OFF"))

    body = res.json()
    assert [i["name"] for i in body["items"]] == ["Kale picado", "Quinoa"]
    assert next(i for i in body["items"] if i["name"] == "Kale picado")["id"] == kale["id"]

    checks = client.get(f"/api/prep/templates/{template['id']}/checks", headers=headers).json()
    names = sorted(e["item_name"] for e in checks[0]["entries"])
    assert names == ["Kale picado", "Tomates cherry"]  # el historial conserva el ítem retirado

    # El ítem retirado ya no se puede llenar.
    res = client.post(f"/api/prep/templates/{template['id']}/checks", json={
        "checkpoint": "3pm", "entries": [{"template_item_id": tomates["id"], "on_hand": "1"}],
    }, headers=headers)
    assert res.status_code == 400


def test_prep_duplicate_item_in_submission_is_400(client, clayton_branch, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)
    item_id = template["items"][0]["id"]
    res = client.post(f"/api/prep/templates/{template['id']}/checks", json={
        "checkpoint": "10am",
        "entries": [{"template_item_id": item_id, "on_hand": "1"}, {"template_item_id": item_id, "on_hand": "2"}],
    }, headers=headers)
    assert res.status_code == 400


def test_prep_day_uses_panama_date(client, clayton_branch, supervisor_user, clayton_device, monkeypatch):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)
    monkeypatch.setattr("routers.prep.hoy_panama", lambda: date(2026, 1, 15))
    res = client.post(f"/api/prep/templates/{template['id']}/checks", json={
        "checkpoint": "8pm", "entries": [{"template_item_id": template["items"][0]["id"], "on_hand": "1"}],
    }, headers=headers)
    assert res.json()["check_date"] == "2026-01-15"


# ---- Pedidos ----

def test_scheduled_order_text_in_panama_time():
    from routers.orders import _build_whatsapp_order_text
    from schemas.order import PublicOrderCreate
    order_in = PublicOrderCreate.model_construct(
        delivery_type="pickup", delivery_address=None, fulfillment_type="scheduled",
        scheduled_for=datetime(2099, 1, 15, 17, 0, tzinfo=timezone.utc), payment_method="yappy",
    )
    texto = _build_whatsapp_order_text("FH-1", "Clayton", [], order_in, Decimal("0"), Decimal("10"))
    assert "Para: 15/01/2099 12:00 PM" in texto


def test_public_order_without_session_does_not_rewrite_existing_contact(client, db_session, clayton_branch, monkeypatch):
    monkeypatch.setattr("routers.orders.resolve_whatsapp_destination", lambda *a, **k: "50760000000")
    contact = Contact(name="Nombre Real", phone="+50761112222", address="Casa real")
    db_session.add(contact)
    db_session.commit()
    sku = "DRK_AGUA"
    res = client.post("/api/orders/public", json={
        "branch_code": "CLY", "delivery_type": "pickup", "payment_method": "yappy",
        "fulfillment_type": "asap", "customer_name": "Otro Nombre", "customer_phone": "+507 6111-2222",
        "items": [{"sku": sku, "quantity": 1}],
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.status_code == 200, res.text
    db_session.refresh(contact)
    assert contact.name == "Nombre Real"


# ---- Bot ----

def _conv_with_bot_message(db_session, branch, phone, minutes_ago):
    contact = Contact(name="Cliente", phone=phone)
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, branch_id=branch.id, status="open")
    db_session.add(conv)
    db_session.flush()
    db_session.add(Message(conversation_id=conv.id, direction="outgoing", sender_type="system",
                           content="¿Qué deseas pedir?", created_at=datetime.utcnow() - timedelta(minutes=minutes_ago)))
    db_session.commit()
    return conv


def test_followup_failure_does_not_block_other_conversations(db_session, clayton_branch, monkeypatch):
    from services import bot_followup
    first = _conv_with_bot_message(db_session, clayton_branch, "+50760000011", 10)
    second = _conv_with_bot_message(db_session, clayton_branch, "+50760000012", 10)
    enviados = []

    async def fake_send(db, wa, conv, contact, phone, text_):
        if conv.id == first.id:
            raise RuntimeError("Meta 131047")
        enviados.append(conv.id)

    monkeypatch.setattr("routers.webhooks._send_plain_text_message", fake_send)
    monkeypatch.setattr(bot_followup, "SessionLocal", lambda: _NonClosing(db_session))
    import asyncio
    asyncio.run(bot_followup._sweep_once())
    assert enviados == [second.id]
    db_session.refresh(first)
    assert first.bot_followup_sent_at is not None  # no se reintenta cada minuto


def test_followup_skips_stale_pauses(db_session, clayton_branch, monkeypatch):
    from services import bot_followup
    _conv_with_bot_message(db_session, clayton_branch, "+50760000013", 60 * 24)
    enviados = []

    async def fake_send(db, wa, conv, contact, phone, text_):
        enviados.append(conv.id)

    monkeypatch.setattr("routers.webhooks._send_plain_text_message", fake_send)
    monkeypatch.setattr(bot_followup, "SessionLocal", lambda: _NonClosing(db_session))
    import asyncio
    asyncio.run(bot_followup._sweep_once())
    assert enviados == []


def test_agent_reply_pauses_bot(client, db_session, clayton_branch, clayton_agent, clayton_device):
    conv = _conv_with_bot_message(db_session, clayton_branch, "+50760000014", 1)
    res = client.post("/api/messages/", json={"conversation_id": conv.id, "content": "Hola, te atiendo yo"},
                      headers=auth_headers_for(clayton_agent, clayton_device.device_id))
    assert res.status_code == 200, res.text
    db_session.refresh(conv)
    assert conv.automation_paused is True


def test_internal_note_does_not_pause_bot(client, db_session, clayton_branch, clayton_agent, clayton_device):
    conv = _conv_with_bot_message(db_session, clayton_branch, "+50760000015", 1)
    client.post("/api/messages/", json={"conversation_id": conv.id, "content": "nota", "is_internal": True},
                headers=auth_headers_for(clayton_agent, clayton_device.device_id))
    db_session.refresh(conv)
    assert conv.automation_paused is False


def test_empty_whatsapp_profile_name_falls_back():
    from services.whatsapp_service import MockWhatsAppService
    parsed = MockWhatsAppService().parse_incoming_message({"entry": [{"changes": [{"value": {
        "messages": [{"from": "50760000000", "id": "wamid.X", "type": "text", "text": {"body": "hola"}}],
        "contacts": [{"profile": {"name": ""}}],
    }}]}]})
    assert parsed["contact_name"] == "Cliente WhatsApp"


# ---- Invu / Link ----

from tests.test_link_sales_sync import invu_ventas, _dia_con_devolucion, DIA  # noqa: E402,F401


def test_daily_sales_orders_exclude_credit_notes(client, db_session, invu_ventas, clayton_branch, admin_user):  # noqa: F811 (fixture importado de test_link_sales_sync)
    from services import invu_client, invu_sales_sync
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 50.4}
    registro = invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)
    assert registro.orders_count == 3  # el diagnóstico de la sincronización cuenta todo lo recibido
    rows = client.get(f"/api/link/sales/daily?date_from={DIA}&date_to={DIA}", headers=auth_headers_for(admin_user)).json()
    assert rows[0]["orders_count"] == 2  # el KPI "Órdenes" no cuenta la nota de crédito


def test_day_that_failed_later_still_shows_its_totals(client, db_session, invu_ventas, clayton_branch, admin_user):  # noqa: F811 (fixture importado de test_link_sales_sync)
    from services import invu_client, invu_sales_sync
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 50.4}
    invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)
    invu_sales_sync._anotar_error(db_session, clayton_branch, DIA, "timeout")
    rows = client.get(f"/api/link/sales/daily?date_from={DIA}&date_to={DIA}", headers=auth_headers_for(admin_user)).json()
    assert len(rows) == 1


def test_empty_invu_response_does_not_wipe_stored_day(db_session, invu_ventas, clayton_branch):  # noqa: F811 (fixture importado de test_link_sales_sync)
    from models.invu_sales import InvuSale
    from services import invu_client, invu_sales_sync
    cred = invu_client.Credenciales("api_cly", "clave")
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_sales_sync.sync_day(db_session, clayton_branch, cred, DIA)
    invu_ventas["ordenes"]["api_cly"] = []
    with pytest.raises(invu_client.InvuError):
        invu_sales_sync.sync_day(db_session, clayton_branch, cred, DIA)
    assert db_session.query(InvuSale).count() == 3


class _NonClosing:
    def __init__(self, session):
        self._s = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._s, name)
