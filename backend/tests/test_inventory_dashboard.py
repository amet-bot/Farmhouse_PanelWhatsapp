"""
Tablero del Resumen: ventas, compras y merma por sucursal, con su % sobre la venta, contra el
período anterior.

Hubo acá también un "costo de lo vendido" / "cobertura de recetas" (cruzaba las ventas con las
recetas de Invu, ver InvuRecipeLine). Se quitó el 2026-10-01 junto con esa métrica del tablero
(routers/inventory.py): con tan pocas recetas cargadas en Invu, el número salía muy por debajo
del costo real y el food cost % que mostraba era engañoso.
"""
from datetime import timedelta
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuSyncDay
from services.invu_sales_sync import hoy_panama
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def test_el_tablero_junta_ventas_compras_y_merma(client, db_session, clayton_branch,
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
    assert "theoretical_cost" not in t and "recipe_coverage_pct" not in t and "food_cost_pct" not in t
    assert Decimal(t["waste_pct_sales"]) == Decimal("1")
    assert Decimal(t["purchases_pct_sales"]) == Decimal("6")
    assert Decimal(d["prev_totals"]["sales_net"]) == Decimal("800")
    assert d["top_waste"][0]["name"] == "Pollo tablero" and Decimal(d["top_waste"][0]["cost"]) == Decimal("10")


def test_un_encargado_ve_solo_su_sucursal(client, db_session, clayton_branch, obarrio_branch, clayton_agent,
                                          clayton_device):
    d = client.get(f"/api/inventory/dashboard?days=30&branch_id={obarrio_branch.id}",
                   headers=_h(clayton_agent, clayton_device)).json()
    assert d["branch_id"] == clayton_branch.id
    assert all(b["branch_id"] == clayton_branch.id for b in d["branches"])
