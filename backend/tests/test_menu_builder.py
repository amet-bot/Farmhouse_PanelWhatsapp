"""
"Arma tu bowl" y demás productos con opciones del Menú Digital (services/menu_builders.py):
el servidor valida lo elegido, cobra las opciones con costo desde el catálogo y deja lo elegido
en la línea del pedido (y en el mensaje de WhatsApp) para la cocina.
"""
import json
from urllib.parse import unquote

from models.order import Order

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _order(items):
    return {
        "branch_code": "CLY",
        "delivery_type": "pickup",
        "payment_method": "card",
        "customer_name": "Cliente Bowl",
        "customer_phone": "6000-3333",
        "items": items,
    }


BYO_OK = {
    "bases": ["quinoa"],
    "toppings": ["tomates_cherry", "roasted_corn"],
    "dressing": ["chipotle_lime"],
    "crunch": ["chilli_crunch"],
}


def _all_products(data):
    return [p for t in data["tabs"] for p in t["products"]]


def test_menu_expone_las_opciones_y_oculta_lo_que_ya_no_esta(client):
    data = client.get("/api/menu/items").json()
    products = _all_products(data)
    titles = {p["title"] for p in products}

    byo = next(p for p in products if p["title"] == "Arma tu bowl")
    assert byo["builder"]["visual"] == "bowl"
    groups = {g["key"]: g for g in byo["builder"]["groups"]}
    assert list(groups) == ["bases", "toppings", "dressing", "crunch"]
    assert (groups["bases"]["min"], groups["bases"]["max"]) == (1, 2)
    assert groups["toppings"]["max"] == 4
    chilli = next(o for o in groups["crunch"]["options"] if o["key"] == "chilli_crunch")
    assert chilli["price"] == 1.0
    assert "price_sku" not in chilli, "El SKU interno no se expone"
    assert byo["addons"]["warm"], "Arma tu bowl lleva los premiums"

    # El chilli crunch es una opción dentro del bowl, no un plato suelto; La Típica ya no se hace.
    assert not any("Chilli" in t for t in titles)
    assert "La Típica" not in titles
    assert "Coconut Cloud" not in titles

    # Solo fotos reales de Farmhouse (las de banco de imágenes no se usan en /menu).
    assert all(p["image_url"] == "" or p["image_url"].startswith("/frontend/static/") for p in products)
    avopesto = next(p for p in products if p["title"] == "Avopesto")
    assert avopesto["image_url"].endswith("/carta/avopesto.webp")

    # Los wraps comparten pestaña con las ensaladas pero no llevan premiums.
    wrap = next(p for p in products if p["title"] == "El Lupito")
    assert not wrap["addons"]["warm"] and not wrap["addons"]["cold"]


def test_pedido_con_bowl_armado_cobra_y_guarda_lo_elegido(client, clayton_branch, db_session):
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "BYO_LRG", "quantity": 1, "addon_skus": ["PRM_STEAK"], "choices": BYO_OK},
    ]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 15.95 (large) + 5.00 (steak) + 1.00 (chilli crunch) = 21.95
    assert data["subtotal"] == "21.95"

    order = db_session.query(Order).filter(Order.order_code == data["order_code"]).first()
    line = json.loads(order.items_json)["items"][0]
    assert line["choices"] == [
        {"title": "Base", "items": ["Quinoa"]},
        {"title": "Toppings", "items": ["Tomates cherry", "Roasted corn"]},
        {"title": "Dressing", "items": ["Chipotle lime"]},
        {"title": "Crunch", "items": ["Chilli crunch"]},
    ]
    assert {a["sku"] for a in line["addons"]} == {"PRM_STEAK", "BYO_CHILLI_CRUNCH"}

    text = unquote(data["whatsapp_url"])
    assert "Base: Quinoa" in text
    assert "Dressing: Chipotle lime" in text


def test_bowl_incompleto_o_con_opciones_de_mas_se_rechaza(client, clayton_branch):
    sin_dressing = {k: v for k, v in BYO_OK.items() if k != "dressing"}
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BYO_REG", "choices": sin_dressing}]))
    assert resp.status_code == 400
    assert "Dressing" in resp.json()["detail"]

    sin_nada = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BYO_REG"}]))
    assert sin_nada.status_code == 400

    cinco_toppings = {**BYO_OK, "toppings": ["pepino", "cilantro", "limon", "zucchini", "pimenton"]}
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BYO_REG", "choices": cinco_toppings}]))
    assert resp.status_code == 400
    assert "hasta 4" in resp.json()["detail"]

    inventada = {**BYO_OK, "bases": ["arroz_frito"]}
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BYO_REG", "choices": inventada}]))
    assert resp.status_code == 400


def test_opciones_simples_y_productos_sin_opciones(client, clayton_branch):
    ok = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "DRK_INFUSIONES", "choices": {"infusion": ["o2"]}},
    ]))
    assert ok.status_code == 200, ok.text
    assert ok.json()["subtotal"] == "4.00"
    assert "Elige tu infusión: Manzanilla" in unquote(ok.json()["whatsapp_url"])

    # Una ensalada no lleva opciones para elegir.
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "SAL_CAESAR_REG", "choices": {"bases": ["quinoa"]}},
    ]))
    assert resp.status_code == 400


def test_producto_que_salio_del_menu_no_se_puede_pedir(client, clayton_branch):
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BWL_TIPICA"}]))
    assert resp.status_code == 400
    assert "ya no está en el menú" in resp.json()["detail"]


def test_toppings_se_pueden_repetir_sin_pasar_del_maximo(client, clayton_branch, db_session):
    # Açaí: 3 de fresa (en vez de fresa, banana y piña) está bien.
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "BWL_ACAI", "choices": {"toppings": ["fresa", "fresa", "fresa"], "crunch": ["cacao_nibs"]}},
    ]))
    assert resp.status_code == 200, resp.text
    order = db_session.query(Order).filter(Order.order_code == resp.json()["order_code"]).first()
    line = json.loads(order.items_json)["items"][0]
    assert line["choices"][0] == {"title": "Toppings", "items": ["3x Fresa"]}
    assert resp.json()["subtotal"] == "8.00"

    # Mezcla de repetidos y uno solo también.
    mix = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "BWL_ACAI", "choices": {"toppings": ["banana", "banana", "pina"], "crunch": ["coco_rallado"]}},
    ]))
    assert mix.status_code == 200, mix.text
    assert "Toppings: 2x Banana, Piña" in unquote(mix.json()["whatsapp_url"])

    # Pero no más de 3 en total.
    cuatro = client.post("/api/orders/public", headers=HEADERS, json=_order([
        {"sku": "BWL_ACAI", "choices": {"toppings": ["fresa", "fresa", "fresa", "fresa"], "crunch": ["cacao_nibs"]}},
    ]))
    assert cuatro.status_code == 400

    # La base no se repite (2x quinoa no es una segunda base).
    base_doble = {**BYO_OK, "bases": ["quinoa", "quinoa"]}
    resp = client.post("/api/orders/public", headers=HEADERS, json=_order([{"sku": "BYO_REG", "choices": base_doble}]))
    assert resp.status_code == 400
