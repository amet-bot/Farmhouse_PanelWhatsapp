"""
Abastecimiento: cuánto le queda a cada sucursal, mínimos y pares, pedido sugerido, órdenes de
compra con líneas (y su recepción prellenada), precios por proveedor y la alerta de stock bajo.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from models.audit import AuditEvent
from models.inventory_item import InventoryItem
from models.shipment import ExpectedShipment, Shipment, ShipmentItem
from models.supplier import Supplier
from models.supply import ExpectedShipmentItem, ItemBranchSetting
from services import supply_alerts
from services.branch_hours import PANAMA_TZ
from tests.conftest import auth_headers_for


@pytest.fixture
def catalogo(db_session):
    pollo = InventoryItem(name="Pollo", unit="kg", category="Proteínas", reference_cost=Decimal("3.00"))
    papa = InventoryItem(name="Papa", unit="kg", category="Vegetales", reference_cost=Decimal("0.80"))
    db_session.add_all([pollo, papa]); db_session.commit()
    db_session.refresh(pollo); db_session.refresh(papa)
    return {"pollo": pollo, "papa": papa}


@pytest.fixture
def proveedores(db_session):
    a = Supplier(name="Avícola Sur")
    b = Supplier(name="PriceSmart")
    db_session.add_all([a, b]); db_session.commit()
    db_session.refresh(a); db_session.refresh(b)
    return {"a": a, "b": b}


def _cargamento(db, branch_id, user_id, item, qty, cost, supplier_id=None, days_ago=1):
    sh = Shipment(branch_id=branch_id, received_by_user_id=user_id, supplier_id=supplier_id, received_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago))
    sh.items.append(ShipmentItem(inventory_item_id=item.id, quantity=Decimal(str(qty)), unit_cost=Decimal(str(cost))))
    db.add(sh); db.commit()
    return sh


# ---- cuánto le queda a cada sucursal -------------------------------------------

def test_listado_por_sucursal_distingue_sin_datos_de_cero(client, db_session, clayton_branch, obarrio_branch, admin_user, supervisor_user, catalogo):
    h = auth_headers_for(admin_user)
    d = client.get("/api/supply/stock", headers=h).json()
    assert {b["name"]: b["has_data"] for b in d["branches"]} == {"Clayton": False, "Obarrio": False} or all(not b["has_data"] for b in d["branches"])
    assert all(c is None for r in d["rows"] for c in r["cells"].values())

    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 20, 2.5)
    d = client.get("/api/supply/stock?q=pollo", headers=h).json()
    por_id = {b["id"]: b for b in d["branches"]}
    assert por_id[clayton_branch.id]["has_data"] is True and por_id[obarrio_branch.id]["has_data"] is False
    fila = d["rows"][0]
    assert fila["name"] == "Pollo" and float(fila["cells"][str(clayton_branch.id)]["stock"]) == 20.0
    assert fila["cells"][str(obarrio_branch.id)] is None
    assert "Proteínas" in d["categories"]


def test_agente_solo_ve_su_sucursal(client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device, supervisor_user, catalogo):
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 5, 2.5)
    d = client.get("/api/supply/stock", headers=auth_headers_for(clayton_agent, clayton_device.device_id)).json()
    assert [b["id"] for b in d["branches"]] == [clayton_branch.id]


# ---- mínimos y pares ------------------------------------------------------------

def test_guardar_minimos_marca_bajo_minimo_y_manda_la_alerta_una_vez_por_dia(client, db_session, clayton_branch, supervisor_user, clayton_device, clayton_agent, catalogo, proveedores, monkeypatch):
    h = auth_headers_for(supervisor_user, clayton_device.device_id)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 5, 2.5)
    r = client.put("/api/supply/settings", json=[{"inventory_item_id": catalogo["pollo"].id, "branch_id": clayton_branch.id, "min_quantity": "10", "par_quantity": "30", "supplier_id": proveedores["a"].id, "lead_days": 2}], headers=h)
    assert r.status_code == 200 and r.json()["saved"] == 1
    assert client.put("/api/supply/settings", json=[{"inventory_item_id": catalogo["pollo"].id, "branch_id": clayton_branch.id, "min_quantity": "10", "par_quantity": "5"}], headers=h).status_code == 400
    # Un agente no edita mínimos.
    assert client.put("/api/supply/settings", json=[{"inventory_item_id": catalogo["pollo"].id, "branch_id": clayton_branch.id, "min_quantity": "1"}], headers=auth_headers_for(clayton_agent, clayton_device.device_id)).status_code == 403

    filas = client.get(f"/api/supply/settings?branch_id={clayton_branch.id}", headers=h).json()["rows"]
    pollo = next(f for f in filas if f["name"] == "Pollo")
    assert pollo["below_min"] is True and float(pollo["stock"]) == 5.0 and pollo["supplier_name"] == "Avícola Sur"
    bajo = client.get("/api/supply/low-stock", headers=h).json()
    assert bajo["total"] == 1 and bajo["branches"][0]["rows"][0]["name"] == "Pollo"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "supply_setting.save").count() == 1

    enviados = []
    monkeypatch.setattr(supply_alerts, "notify_branch_staff", lambda db, bid, title, body, url, **kw: enviados.append((bid, title, body, kw)) or 1)
    ahora = datetime.now(PANAMA_TZ).replace(hour=8)
    assert supply_alerts.send_if_due(now=ahora.replace(hour=5), db=db_session) == 0          # antes de las 7 no
    assert supply_alerts.send_if_due(now=ahora, db=db_session) == 1
    assert supply_alerts.send_if_due(now=ahora, db=db_session) == 0                          # ya se mandó hoy
    assert enviados[0][0] == clayton_branch.id and "Pollo" in enviados[0][2] and enviados[0][3]["managers_only"] is True
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "alert.low_stock").count() == 1


# ---- pedido sugerido y órdenes ---------------------------------------------------

def test_pedido_sugerido_hasta_el_par_descontando_lo_que_viene_y_crear_orden(client, db_session, clayton_branch, supervisor_user, clayton_device, clayton_agent, catalogo, proveedores):
    h = auth_headers_for(supervisor_user, clayton_device.device_id)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 5, 2.5, supplier_id=proveedores["a"].id)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["papa"], 50, 0.7, supplier_id=proveedores["b"].id)
    client.put("/api/supply/settings", json=[{"inventory_item_id": catalogo["pollo"].id, "branch_id": clayton_branch.id, "min_quantity": "10", "par_quantity": "30", "supplier_id": proveedores["a"].id, "lead_days": 2}], headers=h)

    s = client.get(f"/api/supply/suggested?branch_id={clayton_branch.id}", headers=h).json()
    assert s["lines"] == 1
    g = s["groups"][0]
    assert g["supplier_name"] == "Avícola Sur"
    linea = g["lines"][0]
    assert linea["name"] == "Pollo" and float(linea["suggested_qty"]) == 25.0 and "bajo el mínimo" in linea["reasons"]
    assert float(linea["unit_cost"]) == 2.5 and float(linea["est_cost"]) == 62.5

    manana = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)).date().isoformat()
    # Un agente no crea órdenes.
    assert client.post("/api/supply/orders", json={"branch_id": clayton_branch.id, "expected_date": manana, "items": [{"inventory_item_id": catalogo["pollo"].id, "quantity": "10"}]}, headers=auth_headers_for(clayton_agent, clayton_device.device_id)).status_code == 403
    # Insumo repetido, no.
    assert client.post("/api/supply/orders", json={"branch_id": clayton_branch.id, "expected_date": manana, "items": [{"inventory_item_id": catalogo["pollo"].id, "quantity": "10"}, {"inventory_item_id": catalogo["pollo"].id, "quantity": "1"}]}, headers=h).status_code == 400
    r = client.post("/api/supply/orders", json={"branch_id": clayton_branch.id, "supplier_id": proveedores["a"].id, "expected_date": manana, "time_from": "15:00", "notes": "confirmar", "items": [{"inventory_item_id": catalogo["pollo"].id, "quantity": "10"}]}, headers=h)
    assert r.status_code == 201, r.text
    orden = r.json()
    assert orden["status"] == "pendiente" and len(orden["items"]) == 1 and orden["items"][0]["item_name"] == "Pollo"
    assert float(orden["items"][0]["unit_cost"]) == 2.5 and float(orden["est_cost"]) == 25.0     # costo: el último conocido
    assert db_session.query(ExpectedShipmentItem).count() == 1
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "purchase_order.create").count() == 1

    # Lo que ya viene se descuenta del sugerido: 30 - 5 - 10 = 15.
    s = client.get(f"/api/supply/suggested?branch_id={clayton_branch.id}", headers=h).json()
    assert float(s["groups"][0]["lines"][0]["suggested_qty"]) == 15.0 and float(s["groups"][0]["lines"][0]["in_transit"]) == 10.0
    assert len(client.get(f"/api/supply/orders?branch_id={clayton_branch.id}", headers=h).json()) == 1
    # La recepción (Inventario → Cargamentos) ve la orden con sus líneas.
    agendados = client.get(f"/api/inventory/expected-shipments?branch_id={clayton_branch.id}", headers=h).json()
    assert agendados[0]["id"] == orden["id"] and agendados[0]["items"][0]["inventory_item_id"] == catalogo["pollo"].id


def test_lo_que_pide_el_equipo_entra_al_sugerido(client, db_session, clayton_branch, supervisor_user, clayton_device, clayton_agent, catalogo):
    ha = auth_headers_for(clayton_agent, clayton_device.device_id)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["papa"], 50, 0.7)
    r = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "Papa", "inventory_item_id": catalogo["papa"].id, "quantity": "20"}, headers=ha)
    assert r.status_code == 201, r.text
    assert r.json()["inventory_item_id"] == catalogo["papa"].id and float(r.json()["quantity"]) == 20.0
    assert client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "x", "inventory_item_id": 9999}, headers=ha).status_code == 404
    s = client.get(f"/api/supply/suggested?branch_id={clayton_branch.id}", headers=auth_headers_for(supervisor_user, clayton_device.device_id)).json()
    linea = next(l for g in s["groups"] for l in g["lines"] if l["name"] == "Papa")
    assert "lo pidió el equipo" in linea["reasons"] and float(linea["suggested_qty"]) == 20.0


# ---- precios por proveedor --------------------------------------------------------

def test_precios_por_proveedor_marcan_el_mejor(client, db_session, clayton_branch, supervisor_user, clayton_device, catalogo, proveedores):
    h = auth_headers_for(supervisor_user, clayton_device.device_id)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 10, 2.5, supplier_id=proveedores["a"].id, days_ago=10)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 10, 2.9, supplier_id=proveedores["a"].id, days_ago=3)
    _cargamento(db_session, clayton_branch.id, supervisor_user.id, catalogo["pollo"], 10, 2.6, supplier_id=proveedores["b"].id, days_ago=1)
    d = client.get("/api/supply/prices?q=pollo", headers=h).json()
    assert len(d["rows"]) == 2 and len(d["suppliers"]) == 2
    por_prov = {r["supplier_name"]: r for r in d["rows"]}
    a, b = por_prov["Avícola Sur"], por_prov["PriceSmart"]
    assert a["purchases"] == 2 and float(a["last_cost"]) == 2.9 and float(a["min_cost"]) == 2.5 and float(a["avg_cost"]) == 2.7
    assert b["best_price"] is True and a["best_price"] is False and a["vs_best_pct"] == pytest.approx(11.5, abs=0.1)


def test_puesta_en_marcha_dice_que_le_falta_a_cada_sucursal(client, db_session, admin_user, clayton_branch, obarrio_branch, clayton_agent, clayton_device, supervisor_user):
    """Primer conteo, hoja de cierre (y tamaños de pieza que faltan), mínimos y cierres de la
    semana: por sucursal. Un empleado solo ve la suya."""
    pollo = InventoryItem(name="Pollo", unit="kilogramo")
    bolsa = InventoryItem(name="Bolsa", unit="unidad")
    db_session.add_all([pollo, bolsa]); db_session.commit()
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [pollo.id, bolsa.id]}, headers=hs)

    d = client.get("/api/supply/setup", headers=auth_headers_for(admin_user)).json()
    por_nombre = {f["branch"]["name"]: f for f in d["branches"]}
    cly, obr = por_nombre[clayton_branch.name], por_nombre[obarrio_branch.name]
    assert obr["progress"] == 0 and obr["total"] == 4
    assert cly["steps"]["closing_sheet"] == {"done": True, "items": 2, "missing_piece_size": ["Pollo"], "missing_piece_size_count": 1}
    assert cly["steps"]["first_count"]["done"] is False and cly["progress"] == 1

    # Un cierre de turno cuenta como primer conteo y como cierre de la semana.
    client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "lines": [{"inventory_item_id": pollo.id, "counted_quantity": "3"}]}, headers=hs)
    fila = db_session.query(ItemBranchSetting).filter_by(inventory_item_id=bolsa.id, branch_id=clayton_branch.id).one()
    fila.min_quantity = Decimal("50")
    db_session.commit()
    cly = next(f for f in client.get("/api/supply/setup", headers=auth_headers_for(admin_user)).json()["branches"] if f["branch"]["id"] == clayton_branch.id)
    assert cly["progress"] == 4 and cly["steps"]["first_count"]["items"] == 1 and cly["steps"]["closings"]["last_week"] == 1
    assert cly["steps"]["minimums"] == {"done": True, "items": 1}

    propia = client.get("/api/supply/setup", headers=auth_headers_for(clayton_agent, clayton_device.device_id)).json()
    assert [f["branch"]["id"] for f in propia["branches"]] == [clayton_branch.id]
