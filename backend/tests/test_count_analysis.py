"""
Análisis del conteo: lo que tenía que haber (descontando lo que se cocinó según las recetas de
Invu) contra lo que se contó. El primer conteo de un insumo es su punto de partida.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from models.stock_count import StockCount
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, db, name, unit, invu_id=None, ref=None):
    it = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()
    row = db.get(InventoryItem, it["id"])
    row.invu_id = invu_id
    row.reference_cost = Decimal(ref) if ref is not None else None
    db.commit()
    return it


def _contar(client, h, branch_id, **cantidades):
    res = client.post("/api/inventory/counts", json={
        "branch_id": branch_id,
        "items": [{"inventory_item_id": i, "counted_quantity": str(q)} for i, q in cantidades.items()],
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def _vender(db, branch_id, invu_item_id, cantidad, cuando):
    venta = InvuSale(branch_id=branch_id, invu_order_id=int(cuando.timestamp()), business_date=cuando.date(),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("10"))
    db.add(venta)
    db.flush()
    db.add(InvuSaleLine(sale_id=venta.id, branch_id=branch_id, business_date=cuando.date(), invu_line_id=1,
                        invu_item_id=invu_item_id, name="Bowl Pollo", quantity=Decimal(cantidad), counted=True))
    db.commit()


def test_el_conteo_explica_cada_insumo(client, db_session, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    pollo = _item(client, h, db_session, "Pollo", "kg", invu_id=900, ref="5")
    bolsa = _item(client, h, db_session, "Bolsa", "unidad", invu_id=901, ref="0.10")
    queso = _item(client, h, db_session, "Queso", "kg", invu_id=902, ref="8")
    sal = _item(client, h, db_session, "Sal", "kg", invu_id=903)
    # Receta de Invu en Clayton: un Bowl Pollo (plato 7001) lleva 160 g de pollo.
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=7001,
                                  product_invu_id=900, quantity=Decimal("160"), unit_name="gramos"))
    db_session.commit()

    # 1) Primer conteo: todo es punto de partida.
    primero = _contar(client, h, clayton_branch.id, **{str(pollo["id"]): 10, str(bolsa["id"]): 100,
                                                        str(queso["id"]): 5, str(sal["id"]): 2})
    a = primero["analysis"]
    assert a["totals"]["baseline"] == 4 and all(l["status"] == "arranque" for l in a["lines"])
    assert Decimal(a["totals"]["baseline_value"]) == Decimal("50") + Decimal("10") + Decimal("40")

    # El primer conteo "fue ayer"; desde entonces se vendieron 10 bowls (1.6 kg de pollo).
    rec = db_session.get(StockCount, primero["id"])
    rec.counted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db_session.commit()
    _vender(db_session, clayton_branch.id, 7001, 10, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12))

    # 2) Segundo conteo.
    segundo = _contar(client, h, clayton_branch.id, **{str(pollo["id"]): "7.4", str(bolsa["id"]): 80,
                                                        str(queso["id"]): 6, str(sal["id"]): 2})
    lineas = {l["name"]: l for l in segundo["analysis"]["lines"]}

    p = lineas["Pollo"]
    assert p["status"] == "falta"
    assert Decimal(p["used_by_sales"]) == Decimal("1.6")      # 10 bowls × 160 g
    assert Decimal(p["expected"]) == Decimal("8.4")           # 10 − 1.6
    assert Decimal(p["unexplained"]) == Decimal("-1.0")       # contaste 7.4
    assert Decimal(p["cost"]) == Decimal("-5.00") and p["cost_estimated"] is True

    assert lineas["Bolsa"]["status"] == "sin_receta"          # faltan 20, pero ninguna receta la usa
    assert lineas["Queso"]["status"] == "sobra"               # hay 1 kg más: ¿compra sin registrar?
    assert lineas["Sal"]["status"] == "cuadra"

    t = segundo["analysis"]["totals"]
    assert (t["missing"], t["no_recipe"], t["surplus"], t["ok"], t["baseline"]) == (1, 1, 1, 1, 0)
    assert Decimal(t["missing_cost"]) == Decimal("5.00")
    assert Decimal(t["no_recipe_cost"]) == Decimal("2.00")
    assert Decimal(t["surplus_cost"]) == Decimal("8.00")
    assert segundo["analysis"]["recipes_available"] is True
    assert segundo["analysis"]["lines"][0]["name"] == "Pollo"  # primero lo que más falta

    # El análisis se puede volver a pedir y da lo mismo.
    otra = client.get(f"/api/inventory/counts/{segundo['id']}/analysis", headers=h).json()
    assert {l["name"]: l["status"] for l in otra["lines"]} == {n: l["status"] for n, l in lineas.items()}


def test_una_diferencia_chica_cuadra(client, db_session, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, db_session, "Arroz", "kg", ref="2")
    _contar(client, h, clayton_branch.id, **{str(arroz["id"]): 10})
    segundo = _contar(client, h, clayton_branch.id, **{str(arroz["id"]): "9.8"})   # 2 %: balanza
    assert segundo["analysis"]["lines"][0]["status"] == "cuadra"


def test_el_analisis_de_otra_sucursal_no_se_ve(client, db_session, clayton_branch, supervisor_user, clayton_device,
                                              obarrio_agent, obarrio_device):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, db_session, "Arroz blanco", "kg")
    conteo = _contar(client, h, clayton_branch.id, **{str(arroz["id"]): 3})
    res = client.get(f"/api/inventory/counts/{conteo['id']}/analysis", headers=_h(obarrio_agent, obarrio_device))
    assert res.status_code == 403
