"""
Recetas cargadas (dashboard del pasante): importar, emparejar ingredientes con el catálogo,
enlazar platos vendidos, costear (food cost) y usarlas donde Invu no tiene receta.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from routers import inventory as inv
from services import recipe_resolver
from tests.conftest import auth_headers_for

PAYLOAD = {
    "source": "farmhouse_app",
    "recipes": [{"name": "Pesto chicken sandwich", "category": "Toasties", "sale_price": "13.95", "ref_cost": "3.10", "ref_food_cost_pct": "22.2",
                 "lines": [{"name": "Pollo", "quantity": "65", "unit": "g"}, {"name": "Pan - Pan & Canela", "quantity": "1", "unit": "ud"},
                           {"name": "Sal", "quantity": "1", "unit": "g"}]},
                {"name": "Lemon loaf", "category": "Bakery", "sale_price": "1.00",
                 "lines": [{"name": "Pollo", "quantity": "1000", "unit": "g"}]}],
    "internas": [{"name": "Kewpie Mayo", "category": "Salsa", "yield_weight_g": "640", "yield_portions": "16", "ref_cost": "6.40",
                  "lines": [{"name": "Mayonesa", "quantity": "450", "unit": "g"}]}],
    "prices": [{"name": "Pollo", "package_grams": "1000", "supplier": "Proveedor A", "price": "5"},
               {"name": "Pollo", "package_grams": "1000", "supplier": "Proveedor B", "price": "4"}],
}


@pytest.fixture
def catalogo(db_session):
    pollo = InventoryItem(name="Pollo", unit="gramos", invu_id=9)
    pan = InventoryItem(name="Pan artesanal", unit="unidad")          # cargado a mano: sin id de Invu
    db_session.add_all([pollo, pan]); db_session.commit()
    return pollo, pan


def _venta(db, branch, item_id, nombre, cantidad, n):
    hoy = date.today()
    s = InvuSale(branch_id=branch.id, invu_order_id=900 + n, business_date=hoy, status="Cerrada", opened_at=datetime.utcnow() - timedelta(hours=1))
    db.add(s); db.flush()
    db.add(InvuSaleLine(sale_id=s.id, branch_id=branch.id, business_date=hoy, invu_line_id=800 + n, invu_item_id=item_id, name=nombre, quantity=Decimal(cantidad), counted=True))
    db.commit()


def test_importar_emparejar_y_usar_recetas_cargadas(client, db_session, admin_user, supervisor_user, clayton_agent, clayton_device, clayton_branch, catalogo):
    pollo, pan = catalogo
    _venta(db_session, clayton_branch, 500, "Pesto chicken", "3", 1)

    # Solo admin importa.
    assert client.post("/api/recipes/import", json=PAYLOAD, headers=auth_headers_for(supervisor_user, clayton_device.device_id)).status_code == 403
    ha = auth_headers_for(admin_user)
    r = client.post("/api/recipes/import", json=PAYLOAD, headers=ha)
    assert r.status_code == 200, r.text
    assert r.json() == {"recipes": 2, "internas": 1, "prices": 2, "ingredients_auto": 1, "dishes_auto": 1}
    # Importar otra vez actualiza, no duplica.
    assert client.post("/api/recipes/import", json=PAYLOAD, headers=ha).json()["recipes"] == 2
    assert len(client.get("/api/recipes", headers=ha).json()["recipes"]) == 2

    # Ingredientes: Pollo emparejado solo; Pan y Sal pendientes (con sugerencias).
    ing = client.get("/api/recipes/ingredients", headers=ha).json()
    por = {i["name"]: i for i in ing["ingredients"]}
    assert por["Pollo"]["status"] == "emparejado" and por["Pollo"]["auto"] is True
    assert por["Pan - Pan & Canela"]["status"] == "pendiente" and ing["pending"] == 3   # pan, sal, mayonesa
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.put(f"/api/recipes/ingredients/{por['Pan - Pan & Canela']['id']}", json={"inventory_item_id": pan.id}, headers=hs).json()["status"] == "emparejado"
    assert client.put(f"/api/recipes/ingredients/{por['Sal']['id']}", json={"ignored": True}, headers=hs).json()["status"] == "ignorado"
    assert client.put(f"/api/recipes/ingredients/{por['Sal']['id']}", json={"ignored": True}, headers=auth_headers_for(clayton_agent, clayton_device.device_id)).status_code == 403

    # Food cost: pollo 65 g al mejor precio cargado (4/kg) = 0.26; pan sin costo → incompleto.
    recs = {x["name"]: x for x in client.get("/api/recipes", headers=ha).json()["recipes"]}
    p = recs["Pesto chicken sandwich"]
    assert p["mapped_lines"] == 2 and p["total_lines"] == 3 and p["dishes"] == ["Pesto chicken"]
    pollo_linea = next(l for l in p["lines"] if l["name"] == "Pollo")
    assert float(pollo_linea["cost"]) == 0.26 and pollo_linea["cost_source"] == "precio_cargado"
    assert p["flag"] == "incompleto"
    assert recs["Lemon loaf"]["flag"] == "revisar" and float(recs["Lemon loaf"]["food_cost_pct"]) == 400.0   # 4.00 de costo, 1.00 de precio

    # El plato vendido "Pesto chicken" usa la receta cargada: 65 g de pollo y 1 pan por unidad.
    recetas, insumos, origen = recipe_resolver.resolver(db_session, clayton_branch.id)
    assert origen[("item", 500)] == {"source": "cargada", "from_branch": None, "name": "Pesto chicken", "recipe": "Pesto chicken sandwich"}
    uso = inv._uso_por_ventas(db_session, clayton_branch.id, datetime.utcnow() - timedelta(days=1), datetime.utcnow() + timedelta(hours=1))
    assert uso[pollo.id] == Decimal("195") and uso[pan.id] == Decimal("3")

    # Si Invu tiene receta para ese plato en la sucursal, manda Invu.
    db_session.add(InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=500, source_name="Pesto chicken",
                                  product_invu_id=9, product_name="Pollo", quantity=Decimal("80"), unit_name="gramos"))
    db_session.commit()
    _r, _i, origen = recipe_resolver.resolver(db_session, clayton_branch.id)
    assert origen[("item", 500)]["source"] == "invu"


def test_enlace_automatico_solo_si_es_claramente_el_mismo_plato():
    from routers.recipes import _mismo_plato
    from services.recipe_resolver import normalizar as n
    assert _mismo_plato(n("Pesto chicken"), n("Pesto chicken sandwich"))
    assert _mismo_plato(n("Turkey melt"), n("Turkey melt sandwich"))
    assert _mismo_plato(n("Choco-shake"), n("Chocoshake"))
    assert not _mismo_plato(n("Matcha latte"), n("Latte"))
    assert not _mismo_plato(n("Mango"), n("Mango - agua de pipa"))
    assert not _mismo_plato(n("Hummus"), n("Kale hummus"))


def test_platos_preparaciones_y_precios(client, db_session, admin_user, clayton_branch, catalogo):
    _venta(db_session, clayton_branch, 600, "Hot tuna", "5", 2)
    ha = auth_headers_for(admin_user)
    client.post("/api/recipes/import", json=PAYLOAD, headers=ha)

    platos = client.get("/api/recipes/dishes", headers=ha).json()
    hot = next(d for d in platos["dishes"] if d["dish_name"] == "Hot tuna")
    assert hot["recipe"] is None and platos["without_recipe"] >= 1
    rid = next(x["id"] for x in client.get("/api/recipes", headers=ha).json()["recipes"] if x["name"] == "Pesto chicken sandwich")
    assert client.put("/api/recipes/dishes", json={"dish_name": "Hot tuna", "recipe_id": rid}, headers=ha).status_code == 200
    hot = next(d for d in client.get("/api/recipes/dishes", headers=ha).json()["dishes"] if d["dish_name"] == "Hot tuna")
    assert hot["recipe"]["id"] == rid and hot["linked"] is True
    client.put("/api/recipes/dishes", json={"dish_name": "Hot tuna", "recipe_id": None}, headers=ha)
    assert next(d for d in client.get("/api/recipes/dishes", headers=ha).json()["dishes"] if d["dish_name"] == "Hot tuna")["recipe"] is None

    internas = client.get("/api/recipes?kind=interna", headers=ha).json()["recipes"]
    k = internas[0]
    assert k["name"] == "Kewpie Mayo" and float(k["portion_g"]) == 40.0 and float(k["cost_per_g"]) == 0.01   # 6.40 / 640 g

    precios = client.get("/api/recipes/prices", headers=ha).json()["ingredients"]
    pollo = next(x for x in precios if x["name"] == "Pollo")
    assert pollo["suppliers"] == 2 and float(pollo["best_price_per_kg"]) == 4.0
    assert [o["best"] for o in pollo["offers"] if o["supplier"] == "Proveedor B"] == [True]
