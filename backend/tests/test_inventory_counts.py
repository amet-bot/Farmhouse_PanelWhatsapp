"""
Conteo físico: la foto de lo que hay en el estante.

Lo que más se vigila acá es que, después de un conteo, la existencia de cada insumo contado sea
EXACTAMENTE lo contado — sin importar lo que el sistema creía antes, incluido un negativo — y que
lo que no se contó no se toque. El primer conteo de una sucursal hace de inventario de arranque.
"""
from tests.conftest import auth_headers_for


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, headers, name, unit="kg"):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit, "category": "Insumo"},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _shipment(client, headers, branch_id, lineas):
    res = client.post("/api/inventory/shipments", json={"branch_id": branch_id, "items": lineas},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _waste(client, headers, branch_id, lineas):
    res = client.post("/api/inventory/waste", json={"branch_id": branch_id, "reason": "vencido", "items": lineas},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _count(client, headers, branch_id, lineas, notes=None):
    return client.post("/api/inventory/counts", json={"branch_id": branch_id, "notes": notes, "items": lineas},
                       headers=headers)


def _stock_de(client, headers, nombre, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    res = client.get(f"/api/inventory/stock?{query}", headers=headers)
    assert res.status_code == 200, res.text
    return next((f for f in res.json() if f["item_name"] == nombre), None)


# ==========================================================================
# El conteo manda sobre la existencia
# ==========================================================================
def test_despues_de_contar_la_existencia_es_lo_contado(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "20"}])
    _waste(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "2"}])

    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "15"}])
    assert res.status_code == 201, res.text

    linea = res.json()["items"][0]
    assert float(linea["expected_quantity"]) == 18
    assert float(linea["counted_quantity"]) == 15
    assert float(linea["difference"]) == -3

    fila = _stock_de(client, headers, "Tomate")
    assert float(fila["adjusted"]) == -3
    assert float(fila["on_hand"]) == 15
    assert fila["last_counted_at"] is not None


def test_el_conteo_de_arranque_arregla_un_negativo(client, clayton_branch, clayton_agent, clayton_device):
    """El caso por el que existe esto: se mermó algo que entró antes de que el sistema contara."""
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _waste(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "4"}])
    assert float(_stock_de(client, headers, "Tomate")["on_hand"]) == -4

    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "10"}])
    assert res.status_code == 201, res.text
    assert float(res.json()["items"][0]["difference"]) == 14
    assert float(_stock_de(client, headers, "Tomate")["on_hand"]) == 10


def test_los_movimientos_despues_del_conteo_siguen_sumando(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "10"}])

    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "5"}])
    _waste(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "1"}])
    assert float(_stock_de(client, headers, "Tomate")["on_hand"]) == 14

    # Un segundo conteo compara contra lo que el sistema cree AHORA, no contra el primer conteo.
    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "13"}])
    assert float(res.json()["items"][0]["expected_quantity"]) == 14
    assert float(res.json()["items"][0]["difference"]) == -1
    assert float(_stock_de(client, headers, "Tomate")["on_hand"]) == 13


def test_contar_cero_vale(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "3"}])

    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "0"}])
    assert res.status_code == 201, res.text
    assert float(_stock_de(client, headers, "Tomate")["on_hand"]) == 0


def test_lo_que_no_se_conto_no_se_toca(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    cebolla = _item(client, headers, "Cebolla")
    _shipment(client, headers, clayton_branch.id, [
        {"inventory_item_id": tomate["id"], "quantity": "10"},
        {"inventory_item_id": cebolla["id"], "quantity": "8"},
    ])

    _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "9"}])

    assert float(_stock_de(client, headers, "Cebolla")["on_hand"]) == 8
    assert float(_stock_de(client, headers, "Cebolla")["adjusted"]) == 0


def test_un_insumo_solo_contado_aparece_como_movido(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "6"}])

    movidos = client.get("/api/inventory/stock?only_stocked=true", headers=headers).json()
    assert {f["item_name"] for f in movidos} == {"Tomate"}


# ==========================================================================
# Arranque, costo y resumen
# ==========================================================================
def test_el_primer_conteo_de_la_sucursal_es_el_de_arranque(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")

    primero = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "5"}]).json()
    segundo = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "4"}]).json()
    assert primero["is_first_count"] is True
    assert segundo["is_first_count"] is False

    lista = client.get("/api/inventory/counts", headers=headers).json()
    assert [c["is_first_count"] for c in lista] == [False, True]   # el más nuevo primero


def test_el_arranque_es_por_sucursal(client, clayton_branch, obarrio_branch, clayton_agent, clayton_device,
                                     obarrio_agent, obarrio_device):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")

    _count(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "5"}])
    de_obarrio = _count(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "2"}]).json()
    assert de_obarrio["is_first_count"] is True


def test_la_diferencia_se_valua_con_el_ultimo_costo(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    cebolla = _item(client, headers, "Cebolla")
    _shipment(client, headers, clayton_branch.id, [
        {"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "1.00"},
        {"inventory_item_id": cebolla["id"], "quantity": "10", "unit_cost": "0.50"},
    ])
    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "2.00"}])

    conteo = _count(client, headers, clayton_branch.id, [
        {"inventory_item_id": tomate["id"], "counted_quantity": "17"},    # faltan 3 a $2.00
        {"inventory_item_id": cebolla["id"], "counted_quantity": "12"},   # sobran 2 a $0.50
    ]).json()

    assert conteo["mismatched_count"] == 2
    assert float(conteo["difference_cost"]) == -5.00


def test_un_conteo_sin_diferencias_no_tiene_costo(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "1.00"}])

    conteo = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "10"}]).json()
    assert conteo["mismatched_count"] == 0
    assert conteo["difference_cost"] is None


# ==========================================================================
# Validaciones y alcance
# ==========================================================================
def test_un_insumo_repetido_se_rechaza(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    res = _count(client, headers, clayton_branch.id, [
        {"inventory_item_id": tomate["id"], "counted_quantity": "3"},
        {"inventory_item_id": tomate["id"], "counted_quantity": "4"},
    ])
    assert res.status_code == 400


def test_una_cantidad_negativa_se_rechaza(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "-1"}])
    assert res.status_code == 422


def test_un_insumo_inexistente_se_rechaza(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    res = _count(client, headers, clayton_branch.id, [{"inventory_item_id": 9999, "counted_quantity": "1"}])
    assert res.status_code == 404


def test_un_agente_no_puede_contar_en_otra_sucursal(client, obarrio_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    res = _count(client, headers, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "1"}])
    assert res.status_code == 403


def test_un_agente_solo_ve_los_conteos_de_su_sucursal(client, clayton_branch, obarrio_branch,
                                                      clayton_agent, clayton_device,
                                                      obarrio_agent, obarrio_device, admin_user):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")
    _count(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "1"}])
    _count(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "1"}])

    mios = client.get("/api/inventory/counts", headers=h_cla).json()
    assert {c["branch_name"] for c in mios} == {"Clayton"}

    todos = client.get("/api/inventory/counts", headers=_headers(admin_user)).json()
    assert {c["branch_name"] for c in todos} == {"Clayton", "Obarrio"}


def test_el_total_de_la_casa_suma_los_ajustes_de_todas(client, clayton_branch, obarrio_branch,
                                                       clayton_agent, clayton_device,
                                                       obarrio_agent, obarrio_device, admin_user):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")
    _shipment(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "10"}])
    _count(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "8"}])
    _count(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "5"}])

    total = _stock_de(client, _headers(admin_user), "Tomate")
    assert float(total["on_hand"]) == 13


def test_la_merma_despues_de_un_conteo_ve_la_existencia_corregida(client, clayton_branch, clayton_agent,
                                                                  clayton_device):
    """El aviso de "queda en negativo" de la merma tiene que contar el conteo, o sigue mintiendo."""
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _count(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "counted_quantity": "10"}])

    merma = _waste(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "4"}])
    assert float(merma["items"][0]["stock_before"]) == 10
    assert merma["negative_items"] == []
