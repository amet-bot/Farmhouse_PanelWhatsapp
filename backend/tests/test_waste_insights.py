"""
Contexto de una merma al cargarla: semana contra la anterior, el mes, si el motivo se repite, en
qué puesto está el insumo y qué parte de lo usado se botó (con recetas de Invu).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, db, name, unit, ref, invu_id=None):
    it = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()
    row = db.get(InventoryItem, it["id"])
    row.reference_cost = Decimal(ref)
    row.invu_id = invu_id
    db.commit()
    return it


def _merma(client, h, branch_id, item_id, qty, reason="vencido", dias_atras=0):
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias_atras)).isoformat()
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": reason, "occurred_at": cuando,
        "items": [{"inventory_item_id": item_id, "quantity": str(qty)}],
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def test_la_merma_viene_con_su_contexto(client, db_session, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    tomate = _item(client, h, db_session, "Tomate", "kg", "2", invu_id=800)
    lechuga = _item(client, h, db_session, "Lechuga", "kg", "1")
    # Receta: una ensalada (plato 5001) lleva 100 g de tomate; se vendieron 40 hace 3 días (4 kg).
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=5001,
                                  product_invu_id=800, quantity=Decimal("100"), unit_name="gramos"))
    cuando = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
    venta = InvuSale(branch_id=clayton_branch.id, invu_order_id=1, business_date=cuando.date(),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("100"))
    db_session.add(venta)
    db_session.flush()
    db_session.add(InvuSaleLine(sale_id=venta.id, branch_id=clayton_branch.id, business_date=cuando.date(),
                                invu_line_id=1, invu_item_id=5001, name="Ensalada", quantity=Decimal("40"), counted=True))
    db_session.commit()

    _merma(client, h, clayton_branch.id, tomate["id"], "0.5", dias_atras=10)             # semana anterior
    _merma(client, h, clayton_branch.id, tomate["id"], "0.5", dias_atras=2)              # esta semana
    _merma(client, h, clayton_branch.id, lechuga["id"], "3", reason="danado", dias_atras=1)
    nueva = _merma(client, h, clayton_branch.id, tomate["id"], "1")                      # hoy

    ins = nueva["insights"]
    t = ins["items"][0]
    assert t["name"] == "Tomate"
    assert Decimal(t["this_cost"]) == Decimal("2.00")
    assert Decimal(t["week_quantity"]) == Decimal("1.5") and t["week_records"] == 2
    assert Decimal(t["week_cost"]) == Decimal("3.00")
    assert Decimal(t["prev_week_cost"]) == Decimal("1.00")
    assert Decimal(t["month_cost"]) == Decimal("4.00") and t["month_records"] == 3
    assert t["same_reason_month"] == 3                   # tercer "vencido" de tomate en el mes
    assert t["rank_month"] == 1 and t["items_ranked"] == 2   # tomate $4 > lechuga $3
    assert Decimal(t["used_month"]) == Decimal("4")      # 40 ensaladas × 100 g
    assert Decimal(t["waste_pct_month"]) == Decimal("33.3")  # 2 kg botados de 6 kg que pasaron
    assert t["cost_estimated"] is True

    assert Decimal(ins["branch_week_cost"]) == Decimal("6.00")   # tomate 3 + lechuga 3
    assert Decimal(ins["branch_prev_week_cost"]) == Decimal("1.00")
    assert ins["branch_week_records"] == 3

    # Se puede volver a pedir y dice lo mismo.
    otra = client.get(f"/api/inventory/waste/{nueva['id']}/insights", headers=h).json()
    assert otra["items"][0]["same_reason_month"] == 3


def test_el_contexto_de_otra_sucursal_no_se_ve(client, db_session, clayton_branch, clayton_agent, clayton_device,
                                               obarrio_agent, obarrio_device):
    h = _h(clayton_agent, clayton_device)
    pan = _item(client, h, db_session, "Pan", "unidad", "1")
    merma = _merma(client, h, clayton_branch.id, pan["id"], 2)
    res = client.get(f"/api/inventory/waste/{merma['id']}/insights", headers=_h(obarrio_agent, obarrio_device))
    assert res.status_code == 403
