"""
Tablero del Resumen: ventas, compras, merma y costo de lo vendido por sucursal, con su % sobre
la venta, contra el período anterior.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine, InvuSyncDay
from services.invu_sales_sync import hoy_panama
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def test_el_tablero_junta_ventas_compras_merma_y_costo_de_lo_vendido(client, db_session, clayton_branch,
                                                                     obarrio_branch, admin_user):
    h = _h(admin_user)
    hoy = hoy_panama()
    pollo = client.post("/api/inventory/items", json={"name": "Pollo tablero", "unit": "kg"}, headers=h).json()
    row = db_session.get(InventoryItem, pollo["id"])
    row.invu_id, row.reference_cost = 950, Decimal("5")
    db_session.commit()

    # Ventas: $1000 esta semana y $800 la anterior en Clayton.
    db_session.add_all([
        InvuSyncDay(branch_id=clayton_branch.id, business_date=hoy - timedelta(days=1), net_total=Decimal("1000")),
        InvuSyncDay(branch_id=clayton_branch.id, business_date=hoy - timedelta(days=8), net_total=Decimal("800")),
    ])
    # 50 bowls de 200 g de pollo = 10 kg × $5 = $50 de costo teórico; 50 de 100 platos con receta.
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=4001,
                                  product_invu_id=950, quantity=Decimal("200"), unit_name="gramos"))
    cuando = datetime.utcnow() - timedelta(days=1)
    venta = InvuSale(branch_id=clayton_branch.id, invu_order_id=77, business_date=hoy - timedelta(days=1),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("1000"))
    db_session.add(venta)
    db_session.flush()
    db_session.add_all([
        InvuSaleLine(sale_id=venta.id, branch_id=clayton_branch.id, business_date=hoy - timedelta(days=1),
                     invu_line_id=1, invu_item_id=4001, name="Bowl", quantity=Decimal("50"), counted=True),
        InvuSaleLine(sale_id=venta.id, branch_id=clayton_branch.id, business_date=hoy - timedelta(days=1),
                     invu_line_id=2, invu_item_id=4002, name="Jugo", quantity=Decimal("50"), counted=True),
    ])
    db_session.commit()

    # Compra de $60 (12 kg a $5) y merma de 2 kg ($10, al costo de la compra).
    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id,
        "items": [{"inventory_item_id": pollo["id"], "quantity": "12", "unit_cost": "5"}],
    }, headers=h)
    assert res.status_code == 201, res.text
    res = client.post("/api/inventory/waste", json={
        "branch_id": clayton_branch.id, "reason": "vencido",
        "items": [{"inventory_item_id": pollo["id"], "quantity": "2"}],
    }, headers=h)
    assert res.status_code == 201, res.text

    d = client.get("/api/inventory/dashboard?days=7", headers=h).json()
    assert [b["branch_name"] for b in d["branches"]] == [clayton_branch.name]   # Obarrio sin nada, no aparece
    t = d["totals"]
    assert Decimal(t["sales_net"]) == Decimal("1000")
    assert Decimal(t["purchases"]) == Decimal("60")
    assert Decimal(t["waste"]) == Decimal("10")
    assert Decimal(t["theoretical_cost"]) == Decimal("50")
    assert Decimal(t["recipe_coverage_pct"]) == Decimal("50")
    assert Decimal(t["waste_pct_sales"]) == Decimal("1")
    assert Decimal(t["food_cost_pct"]) == Decimal("5")
    assert Decimal(t["purchases_pct_sales"]) == Decimal("6")
    assert Decimal(d["prev_totals"]["sales_net"]) == Decimal("800")
    assert d["top_waste"][0]["name"] == "Pollo tablero" and Decimal(d["top_waste"][0]["cost"]) == Decimal("10")


def test_un_encargado_ve_solo_su_sucursal(client, db_session, clayton_branch, obarrio_branch, clayton_agent,
                                          clayton_device):
    d = client.get(f"/api/inventory/dashboard?days=30&branch_id={obarrio_branch.id}",
                   headers=_h(clayton_agent, clayton_device)).json()
    assert d["branch_id"] == clayton_branch.id
    assert all(b["branch_id"] == clayton_branch.id for b in d["branches"])
