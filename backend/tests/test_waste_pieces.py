"""
Merma por pieza entera o por parte: el servidor pasa "1 baguette entero" o "40 g de un pan" a la
unidad del insumo, aprende cuánto pesa una pieza y no inventa números cuando falta un dato.
"""
from decimal import Decimal

from models.inventory_item import InventoryItem
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, name, unit):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h)
    assert res.status_code in (200, 201), res.text
    return res.json()


def _merma(client, h, branch_id, *lines):
    return client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": "danado", "items": list(lines),
    }, headers=h)


def test_pieza_entera_de_un_insumo_en_gramos_usa_el_peso_de_la_pieza_y_lo_aprende(
        client, db_session, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    pan = _item(client, h, "Pan Baguette", "gramos")

    res = _merma(client, h, clayton_branch.id,
                 {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "2", "piece_size": "80"})
    assert res.status_code == 201, res.text
    linea = res.json()["items"][0]
    assert Decimal(linea["quantity"]) == Decimal("160")      # 2 × 80 g
    assert linea["mode"] == "entera" and Decimal(linea["pieces"]) == 2
    assert Decimal(linea["piece_size"]) == 80

    # La próxima vez ya no hace falta decirlo.
    otra = _merma(client, h, clayton_branch.id, {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "1"})
    assert otra.status_code == 201, otra.text
    assert Decimal(otra.json()["items"][0]["quantity"]) == Decimal("80")


def test_pieza_entera_sin_saber_cuanto_pesa_no_se_guarda(client, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    pan = _item(client, h, "Pan Chico", "gramos")
    res = _merma(client, h, clayton_branch.id, {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "1"})
    assert res.status_code == 400
    assert "Pan Chico" in res.json()["detail"] and "pieza entera" in res.json()["detail"]


def test_insumo_por_unidad_entera_y_parte(client, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    pina = _item(client, h, "Piña entera", "unidad")

    entera = _merma(client, h, clayton_branch.id, {"inventory_item_id": pina["id"], "mode": "entera", "pieces": "3"})
    assert entera.status_code == 201, entera.text
    assert Decimal(entera.json()["items"][0]["quantity"]) == 3

    # Un pedazo de 400 g de una piña de 1600 g = un cuarto de piña.
    parte = _merma(client, h, clayton_branch.id,
                   {"inventory_item_id": pina["id"], "mode": "parte", "part_amount": "400", "piece_size": "1600"})
    assert parte.status_code == 201, parte.text
    assert Decimal(parte.json()["items"][0]["quantity"]) == Decimal("0.25")


def test_parte_de_un_insumo_en_kilos_es_lo_que_marca_la_balanza(client, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    pollo = _item(client, h, "Pollo", "kg")
    res = _merma(client, h, clayton_branch.id, {"inventory_item_id": pollo["id"], "mode": "parte", "quantity": "0.27"})
    assert res.status_code == 201, res.text
    assert Decimal(res.json()["items"][0]["quantity"]) == Decimal("0.27")
    assert res.json()["items"][0]["pieces"] is None


def test_sin_modo_sigue_funcionando_como_antes(client, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    mango = _item(client, h, "Mango", "kg")
    res = _merma(client, h, clayton_branch.id, {"inventory_item_id": mango["id"], "quantity": "1.5"})
    assert res.status_code == 201, res.text
    assert res.json()["items"][0]["mode"] is None


def test_un_agente_no_pisa_el_peso_ya_guardado_pero_un_supervisor_si(
        client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user):
    h = _h(clayton_agent, clayton_device)
    pan = _item(client, h, "Pan Integral", "gramos")
    _merma(client, h, clayton_branch.id,
           {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "1", "piece_size": "80"})

    res = _merma(client, h, clayton_branch.id,
                 {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "1", "piece_size": "500"})
    assert Decimal(res.json()["items"][0]["quantity"]) == Decimal("80")   # vale el guardado

    hs = _h(supervisor_user, clayton_device)
    res = _merma(client, hs, clayton_branch.id,
                 {"inventory_item_id": pan["id"], "mode": "entera", "pieces": "1", "piece_size": "90"})
    assert Decimal(res.json()["items"][0]["quantity"]) == Decimal("90")
    db_session.expire_all()
    assert db_session.get(InventoryItem, pan["id"]).piece_size == Decimal("90")


def test_editar_el_peso_de_una_pieza_es_de_supervisor(client, clayton_agent, clayton_device, supervisor_user):
    h = _h(clayton_agent, clayton_device)
    pan = _item(client, h, "Pan Brioche", "gramos")
    assert client.patch(f"/api/inventory/items/{pan['id']}/piece-size", json={"piece_size": "70"}, headers=h).status_code == 403
    res = client.patch(f"/api/inventory/items/{pan['id']}/piece-size", json={"piece_size": "70"},
                       headers=_h(supervisor_user, clayton_device))
    assert res.status_code == 200, res.text
    assert Decimal(res.json()["piece_size"]) == 70
