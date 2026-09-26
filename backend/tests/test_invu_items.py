"""
Insumos traídos de Invu POS (sus "Ingredientes").

Nunca se habla con Invu de verdad: se reemplazan `iter_ingredients`, `units` e
`ingredient_categories`. Lo que se vigila es lo de este lado: el tipo (casa / materia prima), el
emparejamiento con lo cargado a mano, que la unidad de un insumo con movimientos no se pise, que
los archivados no llenen el catálogo, y que se siga pudiendo crear insumos a mano.
"""
from decimal import Decimal

import pytest

from config import settings
from models.inventory_item import InventoryItem, KIND_HOUSE, KIND_RAW
from services import invu_client
from tests.conftest import auth_headers_for

UNIDADES = {1: "Required Unit", 2: "gramos", 3: "kilogramo", 8: "unidad"}
CATEGORIAS = {3: "Congelados", 8: "Vegetales", 10: "Recetas Finales"}


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _ingrediente(invu_id, nombre, **extra):
    """Una fila como la que devuelve `ingredients/list`."""
    fila = {
        "id": invu_id, "code": f"P{invu_id}", "name": nombre,
        "unit_id": 3, "inventory_unit_id": 3, "category_id": 8,
        "cost": 0, "subrecipe_id": None, "status": 1,
    }
    fila.update(extra)
    return fila


@pytest.fixture
def invu_con(monkeypatch):
    """Enciende la integración (con un usuario de sucursal) y decide qué 'devuelve' Invu."""
    monkeypatch.setattr(settings, "INVU_USER_CLY", "api_cly")
    monkeypatch.setattr(settings, "INVU_PASS_CLY", "clave")
    monkeypatch.setattr(invu_client, "units", lambda: UNIDADES)
    monkeypatch.setattr(invu_client, "ingredient_categories", lambda: CATEGORIAS)

    def _cargar(filas):
        monkeypatch.setattr(invu_client, "iter_ingredients", lambda updated_after=None: iter(filas))
    return _cargar


def _sync(client, user):
    res = client.post("/api/inventory/invu/sync-items", headers=_headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def test_trae_materia_prima_y_preparaciones_de_la_casa(client, db_session, admin_user, invu_con):
    invu_con([
        _ingrediente(211, "Mango Congelado", category_id=3, cost=6.29),
        _ingrediente(205, "Avo Smash", subrecipe_id=44, category_id=10, inventory_unit_id=2, cost="0.0065"),
        _ingrediente(300, "Hierbabuena", unit_id=8, inventory_unit_id=None),   # sin unidad de inventario
        _ingrediente(301, "Tomate viejo", status=0),                          # archivado, nunca sincronizado
    ])
    r = _sync(client, admin_user)
    assert r["received"] == 4 and r["created"] == 3 and r["deactivated"] == 0

    items = {i.invu_id: i for i in db_session.query(InventoryItem).all()}
    assert 301 not in items  # los archivados no llenan el catálogo

    mango = items[211]
    assert (mango.name, mango.code, mango.unit, mango.category, mango.kind) == (
        "Mango Congelado", "P211", "kilogramo", "Congelados", KIND_RAW)
    assert Decimal(mango.reference_cost) == Decimal("6.29")

    avo = items[205]
    assert avo.kind == KIND_HOUSE and avo.unit == "gramos" and avo.category == "Recetas Finales"
    assert Decimal(avo.reference_cost) == Decimal("0.0065")

    assert items[300].unit == "unidad"            # cae a unit_id
    assert items[300].reference_cost is None      # costo 0 = sin referencia

    # La API los devuelve con los datos de Invu.
    lista = client.get("/api/inventory/items?limit=50", headers=_headers(admin_user)).json()
    avo_api = next(i for i in lista if i["name"] == "Avo Smash")
    assert avo_api["kind"] == "casa" and avo_api["code"] == "P205" and avo_api["invu_id"] == 205


def test_empareja_lo_cargado_a_mano_y_no_pisa_la_unidad_si_tiene_movimientos(
    client, db_session, admin_user, clayton_branch, invu_con
):
    h = _headers(admin_user)
    usado = client.post("/api/inventory/items", json={"name": "Zucchini", "unit": "kg"}, headers=h).json()
    sin_uso = client.post("/api/inventory/items", json={"name": "camote", "unit": "lb"}, headers=h).json()
    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "items": [{"inventory_item_id": usado["id"], "quantity": "3"}],
    }, headers=h)
    assert res.status_code == 201, res.text

    invu_con([
        _ingrediente(216, "Zucchini", inventory_unit_id=2),   # en Invu, gramos
        _ingrediente(215, "Camote", inventory_unit_id=3),
    ])
    r = _sync(client, admin_user)
    assert r["linked"] == 2 and r["created"] == 0

    zucchini = db_session.get(InventoryItem, usado["id"])
    db_session.refresh(zucchini)
    assert zucchini.invu_id == 216
    assert zucchini.unit == "kg"   # su cargamento está en kg: no se reinterpreta como gramos

    camote = db_session.get(InventoryItem, sin_uso["id"])
    db_session.refresh(camote)
    assert camote.invu_id == 215 and camote.unit == "kilogramo" and camote.name == "Camote"


def test_archivado_en_invu_se_apaga_y_nombre_repetido_lleva_el_codigo(client, db_session, admin_user, invu_con):
    invu_con([_ingrediente(10, "Eneldo"), _ingrediente(11, "Perejil")])
    _sync(client, admin_user)

    invu_con([
        _ingrediente(10, "Eneldo", status=0),       # se archivó
        _ingrediente(11, "Perejil"),
        _ingrediente(12, "Perejil"),                # otro activo con el mismo nombre
    ])
    r = _sync(client, admin_user)
    assert r["deactivated"] == 1 and r["created"] == 1

    items = {i.invu_id: i for i in db_session.query(InventoryItem).all()}
    assert items[10].active is False
    assert items[11].name == "Perejil"
    assert items[12].name == "Perejil (P12)"

    # Estable: una tercera pasada igual no cambia nada.
    again = _sync(client, admin_user)
    assert again["created"] == 0 and again["updated"] == 0 and again["deactivated"] == 0


def test_con_invu_se_siguen_pudiendo_crear_insumos_a_mano(client, admin_user, invu_con):
    invu_con([])
    res = client.post("/api/inventory/items", json={"name": "Hielo", "unit": "bolsa"}, headers=_headers(admin_user))
    assert res.status_code == 201
    assert res.json()["invu_id"] is None


def test_sin_credenciales_el_boton_avisa(client, admin_user):
    res = client.post("/api/inventory/invu/sync-items", headers=_headers(admin_user))
    assert res.status_code == 503


def test_estado_cuenta_los_insumos(client, admin_user, invu_con):
    invu_con([_ingrediente(1, "Albahaca"), _ingrediente(2, "Huevo")])
    _sync(client, admin_user)
    client.post("/api/inventory/items", json={"name": "Hielo", "unit": "bolsa"}, headers=_headers(admin_user))
    st = client.get("/api/inventory/invu/status", headers=_headers(admin_user)).json()
    assert st["items_synced_count"] == 2 and st["items_local_count"] == 1
    assert st["items_last_synced_at"] is not None
