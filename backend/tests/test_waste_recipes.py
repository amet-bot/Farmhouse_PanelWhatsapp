"""
Merma × recetas de Invu: el uso real de cada insumo sale de platos vendidos × receta (y de los
modificadores elegidos × su receta), en la unidad del insumo, y el % de merma es
merma / (usado + merma).
"""
from datetime import date
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine, InvuSaleModifier
from tests.conftest import auth_headers_for

PERIODO = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _escenario(db, branch):
    kale = InventoryItem(name="Kale", unit="gramos", invu_id=80, code="P80", reference_cost=Decimal("0.005"))
    pollo = InventoryItem(name="Pollo", unit="kilogramo", invu_id=152, code="P152", reference_cost=Decimal("6.00"))
    db.add_all([kale, pollo])
    db.flush()

    # Recetas: "La Cosecha" (plato 4) lleva 30 g de kale; la opción "Pollo Spiced" (20), 160 g de pollo.
    db.add_all([
        InvuRecipeLine(branch_id=branch.id, source_type="item", source_invu_id=4, source_name="La Cosecha",
                       product_invu_id=80, product_code="P80", quantity=Decimal("30"), unit_name="gramos"),
        InvuRecipeLine(branch_id=branch.id, source_type="modifier", source_invu_id=20, source_name="Pollo Spiced",
                       product_invu_id=152, product_code="P152", quantity=Decimal("160"), unit_name="gramos"),
    ])

    # Ventas de marzo: 10 La Cosecha (con pollo) + 1 devuelta por nota de crédito (no cuenta).
    venta = InvuSale(branch_id=branch.id, invu_order_id=1, business_date=date(2026, 3, 10), status="Cerrada")
    db.add(venta)
    db.flush()
    linea = InvuSaleLine(sale_id=venta.id, branch_id=branch.id, business_date=date(2026, 3, 10), invu_line_id=1,
                         invu_item_id=4, name="La Cosecha", quantity=Decimal("10"), counted=True)
    devuelta = InvuSaleLine(sale_id=venta.id, branch_id=branch.id, business_date=date(2026, 3, 10), invu_line_id=2,
                            invu_item_id=4, name="La Cosecha", quantity=Decimal("1"), counted=False)
    db.add_all([linea, devuelta])
    db.flush()
    db.add(InvuSaleModifier(line_id=linea.id, invu_modifier_id=20, name="Pollo Spiced", quantity=Decimal("10")))
    db.commit()
    return kale, pollo


def _merma(client, h, branch_id, item_id, qty):
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": "vencido", "occurred_at": "2026-03-12T15:00:00Z",
        "items": [{"inventory_item_id": item_id, "quantity": qty}],
    }, headers=h)
    assert res.status_code == 201, res.text


def test_merma_contra_uso_real_de_las_recetas(client, db_session, clayton_branch, admin_user):
    h = _h(admin_user)
    kale, pollo = _escenario(db_session, clayton_branch)
    _merma(client, h, clayton_branch.id, kale.id, "30")      # 30 g de kale
    _merma(client, h, clayton_branch.id, pollo.id, "0.2")    # 0.2 kg de pollo

    res = client.get("/api/inventory/waste/recipe-usage", params=PERIODO, headers=h)
    assert res.status_code == 200, res.text
    a = res.json()
    filas = {f["name"]: f for f in a["items"]}

    # Kale: 10 platos × 30 g = 300 g usados; merma 30 → 30 / 330 = 9.1 %.
    assert Decimal(filas["Kale"]["used"]) == Decimal("300")
    assert Decimal(filas["Kale"]["waste_pct"]) == Decimal("9.1")
    assert filas["Kale"]["dishes"][0]["name"] == "La Cosecha" and filas["Kale"]["dishes"][0]["type"] == "plato"

    # Pollo: 10 × 160 g = 1.6 kg (convertido a la unidad del insumo); 0.2 / 1.8 = 11.1 %.
    assert Decimal(filas["Pollo"]["used"]) == Decimal("1.6")
    assert Decimal(filas["Pollo"]["waste_pct"]) == Decimal("11.1")
    assert filas["Pollo"]["dishes"][0] == {"name": "Pollo Spiced", "type": "modificador", "used": "1.600", "share_pct": "100.0"}

    # El más alto primero; y la merma en $ se reparte a los platos que usan cada insumo.
    assert a["items"][0]["name"] == "Pollo"
    platos = {p["name"]: p for p in a["dishes"]}
    assert Decimal(platos["Pollo Spiced"]["allocated_cost"]) == Decimal("1.20")   # 0.2 kg × $6
    assert Decimal(platos["La Cosecha"]["allocated_cost"]) == Decimal("0.15")     # 30 g × $0.005
    assert a["recipes_count"] == 2
    assert Decimal(a["sold_units"]) == Decimal("10") and Decimal(a["sold_units_with_recipe"]) == Decimal("10")


def test_insumo_sin_receta_muestra_la_merma_sin_porcentaje(client, db_session, clayton_branch, admin_user):
    h = _h(admin_user)
    hielo = InventoryItem(name="Hielo", unit="bolsa")
    db_session.add(hielo)
    db_session.commit()
    _merma(client, h, clayton_branch.id, hielo.id, "2")
    fila = next(f for f in client.get("/api/inventory/waste/recipe-usage", params=PERIODO, headers=h).json()["items"])
    assert fila["name"] == "Hielo" and fila["used"] is None and fila["waste_pct"] is None and fila["dishes"] == []


def test_cada_sucursal_ve_sus_recetas(client, db_session, clayton_branch, obarrio_agent, obarrio_device):
    _escenario(db_session, clayton_branch)
    a = client.get("/api/inventory/waste/recipe-usage", params=PERIODO, headers=_h(obarrio_agent, obarrio_device)).json()
    assert a["recipes_count"] == 0 and Decimal(a["sold_units"]) == Decimal("0")


def test_traer_recetas_pide_permiso(client, clayton_agent, clayton_device):
    res = client.post("/api/inventory/invu/sync-recipes", headers=_h(clayton_agent, clayton_device))
    assert res.status_code == 403
