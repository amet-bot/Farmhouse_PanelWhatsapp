"""
Recetas efectivas: la de Invu de la sucursal manda; si no hay, la del mismo plato en otra
sucursal; si es reventa (mismo nombre que un insumo por unidad), 1 unidad; si no, sin receta.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from routers import inventory as inv
from services import recipe_resolver
from tests.conftest import auth_headers_for


def _venta(db, branch, item_id, nombre, cantidad, dia, n):
    sale = InvuSale(branch_id=branch.id, invu_order_id=1000 + n, business_date=dia, status="Cerrada",
                    opened_at=datetime.combine(dia, datetime.min.time()) + timedelta(hours=17))
    db.add(sale); db.flush()
    db.add(InvuSaleLine(sale_id=sale.id, branch_id=branch.id, business_date=dia, invu_line_id=5000 + n,
                        invu_item_id=item_id, name=nombre, quantity=Decimal(cantidad), counted=True))


def _escenario(db, clayton, obarrio):
    pollo = InventoryItem(name="Pollo", unit="gramos", invu_id=9)
    coca = InventoryItem(name="Coca Cola Zero", unit="unidad", invu_id=50)
    agua = InventoryItem(name="Agua", unit="gramos", invu_id=51)          # ingrediente, no reventa
    db.add_all([pollo, coca, agua]); db.commit()
    # Clayton tiene la receta de "La Lupita" (id 100 en su Invu): 150 g de pollo.
    db.add(InvuRecipeLine(branch_id=clayton.id, source_type="item", source_invu_id=100, source_name="La Lupita",
                          product_invu_id=9, product_name="Pollo", quantity=Decimal("150"), unit_name="gramos"))
    # Obarrio tiene su propia receta de "El César" (id 300): manda sobre cualquier otra.
    db.add(InvuRecipeLine(branch_id=obarrio.id, source_type="item", source_invu_id=300, source_name="El César",
                          product_invu_id=9, product_name="Pollo", quantity=Decimal("90"), unit_name="gramos"))
    db.add(InvuRecipeLine(branch_id=clayton.id, source_type="item", source_invu_id=301, source_name="El Cesar",
                          product_invu_id=9, product_name="Pollo", quantity=Decimal("500"), unit_name="gramos"))
    hoy = date.today()
    # Obarrio vende "La lupita" con OTRO id de Invu (200), Coca Cola Zero, Agua y un plato sin receta.
    _venta(db, obarrio, 200, "La lupita ", "4", hoy, 1)
    _venta(db, obarrio, 210, "Coca Cola Zero", "6", hoy, 2)
    _venta(db, obarrio, 220, "Agua", "3", hoy, 3)
    _venta(db, obarrio, 230, "Pesto chicken", "10", hoy, 4)
    _venta(db, obarrio, 300, "El César", "2", hoy, 5)
    _venta(db, obarrio, 310, "El Cesar", "1", hoy, 6)    # versión delivery del mismo plato, sin receta propia
    db.commit()
    return pollo, coca, agua


def test_las_recetas_se_completan_con_otra_sucursal_y_reventa(db_session, clayton_branch, obarrio_branch):
    pollo, coca, agua = _escenario(db_session, clayton_branch, obarrio_branch)
    recetas, insumos, origen = recipe_resolver.resolver(db_session, obarrio_branch.id)
    assert origen[("item", 200)] == {"source": "otra_sucursal", "from_branch": clayton_branch.name, "name": "La lupita "}
    assert origen[("item", 210)]["source"] == "reventa"
    assert ("item", 220) not in origen and ("item", 230) not in origen      # agua (gramos) y pesto: sin receta
    assert origen[("item", 300)]["source"] == "invu" and recetas[("item", 300)][0].quantity == Decimal("90")
    # La versión delivery toma la receta de su gemela de ESTA sucursal (90 g), no la de Clayton (500 g).
    assert origen[("item", 310)]["source"] == "misma_sucursal" and recetas[("item", 310)][0].quantity == Decimal("90")

    # El uso por ventas de Obarrio ya cuenta la receta prestada y la reventa.
    ayer = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    uso = inv._uso_por_ventas(db_session, obarrio_branch.id, ayer, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1))
    assert uso[pollo.id] == Decimal("4") * 150 + Decimal("3") * 90       # 600 g por Lupita + 270 g por los César
    assert uso[coca.id] == Decimal("6")
    assert agua.id not in uso
    assert {pollo.id, coca.id} <= inv._insumos_con_receta(db_session, obarrio_branch.id)

    assert recipe_resolver.normalizar("Galleta De Avena &amp Choco") == recipe_resolver.normalizar("galleta de avena & choco")
    # Nombres de Invu con acentos dañados (UTF-8 leído como Latin-1) se reparan antes de comparar.
    assert recipe_resolver.normalizar("AÃ§aÃ­ bowl") == recipe_resolver.normalizar("Açaí bowl") == "acai bowl"
    assert recipe_resolver.reparar_acentos("Iced Chamomile + LimÃ³n") == "Iced Chamomile + Limón"


def test_reporte_de_cobertura_de_recetas(client, db_session, admin_user, clayton_branch, obarrio_branch):
    _escenario(db_session, clayton_branch, obarrio_branch)
    d = client.get("/api/reports/recipes/coverage", headers=auth_headers_for(admin_user)).json()
    obr = next(b for b in d["branches"] if b["branch"]["id"] == obarrio_branch.id)
    assert obr["dishes"] == 6 and float(obr["units"]) == 26
    assert [m["name"] for m in obr["missing"]] == ["Pesto chicken", "Agua"]          # lo más vendido primero
    assert obr["missing"][0]["versions"] == 1 and float(obr["missing"][0]["sold"]) == 10
    assert obr["covered_pct"] == 50.0                                                 # 13 de 26 unidades
    assert obr["by_source"]["misma_sucursal"]["dishes"] == 1
    assert obr["by_source"]["otra_sucursal"]["dishes"] == 1 and obr["by_source"]["reventa"]["dishes"] == 1
    fila = next(r for r in obr["rows"] if r["invu_item_id"] == 200)
    assert fila["source"] == "otra_sucursal" and fila["from_branch"] == clayton_branch.name and fila["ingredients"] == 1
