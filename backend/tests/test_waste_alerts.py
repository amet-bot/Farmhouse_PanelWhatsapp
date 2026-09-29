"""
Merma que avisa y merma que explica:
  1) Se avisa al encargado cuando una merma cuesta mucho de una vez o cuando el mismo insumo se
     bota por el mismo motivo varias veces en el mes (el residuo al limpiar no avisa).
  2) Si algo se venció, se compara la última compra con el ritmo de uso: para cuántos días
     alcanzaba, cuánto duró y cuánto se alcanza a usar antes de que venza.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from routers import inventory as inv_router
from tests.conftest import auth_headers_for

_orden = iter(range(700000, 800000))


@pytest.fixture(autouse=True)
def avisos(monkeypatch):
    enviados = []
    monkeypatch.setattr(inv_router, "_avisar_merma_background",
                        lambda branch_id, title, body, url, tag: enviados.append((branch_id, title, body, url)))
    return enviados


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, db, name, unit, invu_id=None):
    it = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()
    if invu_id is not None:
        db.get(InventoryItem, it["id"]).invu_id = invu_id
        db.commit()
    return it


def _merma(client, h, branch_id, item_id, qty, costo, reason="vencido", **extra):
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": reason,
        "items": [{"inventory_item_id": item_id, "quantity": str(qty), "unit_cost": str(costo)}], **extra,
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def test_merma_cara_avisa_al_encargado(client, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    salmon = client.post("/api/inventory/items", json={"name": "Salmón", "unit": "kg"}, headers=h).json()
    chica = _merma(client, h, clayton_branch.id, salmon["id"], "1", "5", reason="danado")
    assert chica["alert_reasons"] == [] and avisos == []

    cara = _merma(client, h, clayton_branch.id, salmon["id"], "2", "12.5", reason="danado")
    assert cara["alert_reasons"] == ["Se pierden $25.00 de una vez"]
    assert cara["notified"] is False           # nadie tiene notificaciones activadas en la prueba
    assert len(avisos) == 1
    assert avisos[0][1].startswith("Merma importante") and "Salmón" in avisos[0][2]
    assert avisos[0][3] == f"/inventario?view=merma&waste={cara['id']}"


def test_el_mismo_motivo_repetido_avisa(client, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    tomate = client.post("/api/inventory/items", json={"name": "Tomate", "unit": "kg"}, headers=h).json()
    _merma(client, h, clayton_branch.id, tomate["id"], "0.5", "1")
    _merma(client, h, clayton_branch.id, tomate["id"], "0.5", "1")
    assert avisos == []
    tercera = _merma(client, h, clayton_branch.id, tomate["id"], "0.5", "1")
    assert tercera["alert_reasons"] == ["Tomate: 3.ª vez en el mes por «vencido»"]
    assert len(avisos) == 1


def test_el_residuo_no_avisa(client, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    pollo = client.post("/api/inventory/items", json={"name": "Pollo", "unit": "kg"}, headers=h).json()
    for _ in range(3):
        r = _merma(client, h, clayton_branch.id, pollo["id"], "5", "6", reason="recorte")
    assert r["alert_reasons"] == [] and avisos == []


def test_vencido_compara_la_compra_con_el_uso(client, db_session, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    lechuga = _item(client, h, db_session, "Lechuga", "kg", invu_id=950)
    # Un plato lleva 100 g de lechuga; en el mes se vendieron 30 → 3 kg al mes = 0.1 kg por día.
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=7201,
                                  product_invu_id=950, quantity=Decimal("100"), unit_name="gramos"))
    cuando = datetime.utcnow() - timedelta(days=10)
    venta = InvuSale(branch_id=clayton_branch.id, invu_order_id=next(_orden), business_date=cuando.date(),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("100"))
    db_session.add(venta)
    db_session.flush()
    db_session.add(InvuSaleLine(sale_id=venta.id, branch_id=clayton_branch.id, business_date=cuando.date(),
                                invu_line_id=1, invu_item_id=7201, name="Ensalada", quantity=Decimal("30"), counted=True))
    db_session.commit()

    # Se compraron 10 kg hace 5 días.
    compra = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id,
        "received_at": (datetime.now(timezone.utc) - timedelta(days=5, hours=1)).isoformat(),
        "items": [{"inventory_item_id": lechuga["id"], "quantity": "10", "unit_cost": "2"}],
    }, headers=h)
    assert compra.status_code == 201, compra.text

    merma = _merma(client, h, clayton_branch.id, lechuga["id"], "6", "2")
    it = merma["insights"]["items"][0]
    assert Decimal(it["last_purchase_qty"]) == Decimal("10")
    assert it["days_to_expire"] == 5
    assert Decimal(it["used_per_day"]) == Decimal("0.1")
    assert Decimal(it["purchase_cover_days"]) == Decimal("100.0")   # 10 kg a 0.1 kg por día
    assert Decimal(it["suggested_max_qty"]) == Decimal("0.5")       # lo que se usa en los 5 días que duró
    assert it["expired_90d"] == 1


def test_vencido_sin_receta_informa_la_compra_sin_sugerir(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    queso = client.post("/api/inventory/items", json={"name": "Queso", "unit": "kg"}, headers=h).json()
    client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id,
        "received_at": (datetime.now(timezone.utc) - timedelta(days=8, hours=1)).isoformat(),
        "items": [{"inventory_item_id": queso["id"], "quantity": "4"}],
    }, headers=h)
    it = _merma(client, h, clayton_branch.id, queso["id"], "1", "8")["insights"]["items"][0]
    assert it["days_to_expire"] == 8 and Decimal(it["last_purchase_qty"]) == Decimal("4")
    assert it["used_per_day"] is None and it["suggested_max_qty"] is None
    # Otro motivo que no es "vencido" no trae nada de esto.
    otro = _merma(client, h, clayton_branch.id, queso["id"], "1", "8", reason="danado")["insights"]["items"][0]
    assert otro["last_purchase_qty"] is None
