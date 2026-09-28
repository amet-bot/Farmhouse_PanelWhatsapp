"""
Contexto de un cargamento al registrarlo: precio contra la compra anterior, contra otra sucursal
y contra Invu; existencia y días que alcanza; gasto de la semana y con el proveedor.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from models.supplier import Supplier
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _cargar(client, h, branch_id, lineas, supplier_id=None, dias_atras=0):
    res = client.post("/api/inventory/shipments", json={
        "branch_id": branch_id, "supplier_id": supplier_id,
        "received_at": (datetime.now(timezone.utc) - timedelta(days=dias_atras)).isoformat(),
        "items": [{"inventory_item_id": i, "quantity": str(q), "unit_cost": (str(c) if c is not None else None)}
                  for i, q, c in lineas],
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def test_el_cargamento_viene_con_su_contexto(client, db_session, clayton_branch, obarrio_branch, admin_user):
    h = _h(admin_user)
    prov = Supplier(name="Frutería Uno", active=True)
    otro = Supplier(name="Frutería Dos", active=True)
    db_session.add_all([prov, otro])
    db_session.commit()
    aguacate = client.post("/api/inventory/items", json={"name": "Aguacate", "unit": "kg"}, headers=h).json()
    row = db_session.get(InventoryItem, aguacate["id"])
    row.invu_id, row.reference_cost = 700, Decimal("3.00")
    db_session.commit()
    # Receta: un toast (plato 6001) lleva 50 g de aguacate; se vendieron 140 en 14 días = 7 kg → 0.5 kg por día.
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=6001,
                                  product_invu_id=700, quantity=Decimal("50"), unit_name="gramos"))
    cuando = datetime.utcnow() - timedelta(days=5)
    venta = InvuSale(branch_id=clayton_branch.id, invu_order_id=9, business_date=cuando.date(),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("1"))
    db_session.add(venta)
    db_session.flush()
    db_session.add(InvuSaleLine(sale_id=venta.id, branch_id=clayton_branch.id, business_date=cuando.date(),
                                invu_line_id=1, invu_item_id=6001, name="Toast", quantity=Decimal("140"), counted=True))
    db_session.commit()

    _cargar(client, h, clayton_branch.id, [(aguacate["id"], 5, "4.00")], supplier_id=prov.id, dias_atras=10)   # semana anterior
    _cargar(client, h, obarrio_branch.id, [(aguacate["id"], 5, "3.60")], supplier_id=otro.id, dias_atras=3)    # otra sucursal
    nuevo = _cargar(client, h, clayton_branch.id, [(aguacate["id"], 10, "4.40")], supplier_id=prov.id)

    ins = nuevo["insights"]
    a = ins["items"][0]
    assert Decimal(a["prev_unit_cost"]) == Decimal("4.00") and a["prev_supplier"] == "Frutería Uno"
    assert Decimal(a["change_pct"]) == Decimal("10.0")                       # 4.00 → 4.40
    assert Decimal(a["best_other_cost"]) == Decimal("3.60") and a["best_other_branch"] == obarrio_branch.name
    assert a["best_other_supplier"] == "Frutería Dos"
    assert Decimal(a["reference_cost"]) == Decimal("3.00")
    assert Decimal(a["stock_now"]) == Decimal("15")                          # 5 + 10 (sin merma ni conteo)
    assert Decimal(a["used_per_day"]) == Decimal("0.5")
    assert Decimal(a["days_left"]) == Decimal("30.0")

    assert Decimal(ins["total_cost"]) == Decimal("44.00")
    assert Decimal(ins["branch_week_spend"]) == Decimal("44.00")
    assert Decimal(ins["branch_prev_week_spend"]) == Decimal("20.00")
    assert Decimal(ins["supplier_month_spend"]) == Decimal("64.00")         # 20 + 44 a Frutería Uno
    assert ins["items_without_cost"] == 0

    otra_vez = client.get(f"/api/inventory/shipments/{nuevo['id']}/insights", headers=h).json()
    assert Decimal(otra_vez["items"][0]["change_pct"]) == Decimal("10.0")


def test_sin_costo_se_avisa_y_no_se_compara(client, db_session, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    sal = client.post("/api/inventory/items", json={"name": "Sal gruesa", "unit": "kg"}, headers=h).json()
    nuevo = _cargar(client, h, clayton_branch.id, [(sal["id"], 2, None)])
    ins = nuevo["insights"]
    assert ins["items_without_cost"] == 1 and ins["total_cost"] is None
    assert ins["items"][0]["change_pct"] is None


def test_el_contexto_de_otra_sucursal_no_se_ve(client, db_session, clayton_branch, clayton_agent, clayton_device,
                                               obarrio_agent, obarrio_device):
    h = _h(clayton_agent, clayton_device)
    pan = client.post("/api/inventory/items", json={"name": "Pan de molde", "unit": "unidad"}, headers=h).json()
    nuevo = _cargar(client, h, clayton_branch.id, [(pan["id"], 3, "1.20")])
    res = client.get(f"/api/inventory/shipments/{nuevo['id']}/insights", headers=_h(obarrio_agent, obarrio_device))
    assert res.status_code == 403
