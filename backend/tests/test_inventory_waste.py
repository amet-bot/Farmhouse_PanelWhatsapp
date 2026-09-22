"""
Merma y existencias: la salida que le faltaba a Inventario.

Lo que más se vigila acá es el cálculo de la existencia (entradas menos mermas, sin tabla de
saldos) y la decisión de negocio de **no bloquear** cuando la merma supera lo que hay: el
sistema empezó a contar entradas hace poco y nadie cargó el inventario de arranque, así que un
negativo es información, no un error que haya que impedir.
"""
from tests.conftest import auth_headers_for


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, headers, name, unit="kg"):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit, "category": "Insumo"},
                      headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _shipment(client, headers, branch_id, lineas, supplier_id=None):
    res = client.post("/api/inventory/shipments", json={
        "branch_id": branch_id, "supplier_id": supplier_id, "items": lineas,
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _waste(client, headers, branch_id, lineas, reason="vencido", notes=None):
    return client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": reason, "notes": notes, "items": lineas,
    }, headers=headers)


def _stock_de(client, headers, nombre, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    res = client.get(f"/api/inventory/stock?{query}", headers=headers)
    assert res.status_code == 200, res.text
    return next((f for f in res.json() if f["item_name"] == nombre), None)


# ==========================================================================
# Motivos
# ==========================================================================
def test_los_motivos_los_sirve_el_servidor(client, clayton_agent, clayton_device):
    res = client.get("/api/inventory/waste/reasons", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 200
    codigos = [m["code"] for m in res.json()]
    assert "vencido" in codigos
    assert "consumo_interno" in codigos
    assert "otro" in codigos
    # Cada código viaja con su etiqueta: el frontend no tiene que traducir nada.
    assert all(m["label"] for m in res.json())


def test_un_motivo_inventado_se_rechaza(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    res = _waste(client, headers, clayton_branch.id,
                 [{"inventory_item_id": tomate["id"], "quantity": "1"}], reason="se_lo_comio_el_perro")
    assert res.status_code == 400


# ==========================================================================
# Registrar merma
# ==========================================================================
def test_una_merma_descuenta_de_la_existencia(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")

    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "20", "unit_cost": "1.50"}])

    fila = _stock_de(client, headers, "Tomate")
    assert float(fila["entered"]) == 20
    assert float(fila["wasted"]) == 0
    assert float(fila["on_hand"]) == 20

    res = _waste(client, headers, clayton_branch.id,
                 [{"inventory_item_id": tomate["id"], "quantity": "3.5"}], reason="vencido")
    assert res.status_code == 201, res.text

    fila = _stock_de(client, headers, "Tomate")
    assert float(fila["wasted"]) == 3.5
    assert float(fila["on_hand"]) == 16.5


def test_el_costo_se_copia_del_ultimo_cargamento(client, clayton_branch, clayton_agent, clayton_device):
    """
    Quien registra una merma no sabe cuánto costó el insumo, y no tiene por qué. El servidor lo
    toma del último cargamento de ese insumo en esa sucursal, así la pérdida queda valuada sola.
    """
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")

    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "1.00"}])
    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "2.50"}])

    merma = _waste(client, headers, clayton_branch.id,
                   [{"inventory_item_id": tomate["id"], "quantity": "4"}]).json()

    assert float(merma["items"][0]["unit_cost"]) == 2.50   # el último, no el primero
    assert float(merma["total_cost"]) == 10.00


def test_el_costo_no_se_toma_de_otra_sucursal(client, clayton_branch, obarrio_branch,
                                              clayton_agent, clayton_device,
                                              obarrio_agent, obarrio_device):
    """El mismo insumo puede costar distinto en cada sucursal; valuarlo con el precio ajeno
    inventaría una pérdida que no fue."""
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")

    _shipment(client, h_cla, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "1.00"}])

    # Obarrio nunca recibió tomate: su merma no puede heredar el costo de Clayton.
    merma = _waste(client, h_oba, obarrio_branch.id,
                   [{"inventory_item_id": tomate["id"], "quantity": "2"}]).json()
    assert merma["items"][0]["unit_cost"] is None
    assert merma["total_cost"] is None


def test_un_costo_explicito_le_gana_al_del_cargamento(client, clayton_branch, clayton_agent,
                                                      clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "1.00"}])

    merma = _waste(client, headers, clayton_branch.id,
                   [{"inventory_item_id": tomate["id"], "quantity": "2", "unit_cost": "3.00"}]).json()
    assert float(merma["items"][0]["unit_cost"]) == 3.00


# ==========================================================================
# Existencia negativa: avisa, no bloquea
# ==========================================================================
def test_mermar_mas_de_lo_que_hay_se_permite_y_se_avisa(client, clayton_branch, clayton_agent,
                                                        clayton_device):
    """
    Decisión de negocio, no descuido: nadie cargó el inventario de arranque, así que el stock
    calculado nace más bajo que el real. Bloquear haría el módulo inusable el día uno.
    """
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "5"}])

    res = _waste(client, headers, clayton_branch.id,
                 [{"inventory_item_id": tomate["id"], "quantity": "8"}])
    assert res.status_code == 201, res.text

    merma = res.json()
    assert merma["negative_items"] == ["Tomate"]
    assert float(merma["items"][0]["stock_before"]) == 5

    fila = _stock_de(client, headers, "Tomate")
    assert float(fila["on_hand"]) == -3   # el negativo queda a la vista


def test_una_merma_que_entra_en_la_existencia_no_avisa(client, clayton_branch, clayton_agent,
                                                       clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id,
              [{"inventory_item_id": tomate["id"], "quantity": "5"}])

    merma = _waste(client, headers, clayton_branch.id,
                   [{"inventory_item_id": tomate["id"], "quantity": "5"}]).json()
    assert merma["negative_items"] == []


# ==========================================================================
# Alcance por sucursal
# ==========================================================================
def test_un_agente_no_puede_mermar_en_otra_sucursal(client, obarrio_branch, clayton_agent,
                                                    clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    res = _waste(client, headers, obarrio_branch.id,
                 [{"inventory_item_id": tomate["id"], "quantity": "1"}])
    assert res.status_code == 403


def test_la_existencia_de_un_agente_es_la_de_su_sucursal(client, clayton_branch, obarrio_branch,
                                                         clayton_agent, clayton_device,
                                                         obarrio_agent, obarrio_device):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")

    _shipment(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "20"}])
    _shipment(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "7"}])

    assert float(_stock_de(client, h_cla, "Tomate")["on_hand"]) == 20
    assert float(_stock_de(client, h_oba, "Tomate")["on_hand"]) == 7

    # Un agente que pide otra sucursal igual ve la suya: el filtro no es una sugerencia.
    ajeno = _stock_de(client, h_cla, "Tomate", branch_id=obarrio_branch.id)
    assert float(ajeno["on_hand"]) == 20


def test_el_admin_sin_filtro_ve_el_total_de_la_casa(client, clayton_branch, obarrio_branch,
                                                    clayton_agent, clayton_device,
                                                    obarrio_agent, obarrio_device, admin_user):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")

    _shipment(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "20"}])
    _shipment(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "7"}])
    _waste(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "2"}])

    h_admin = _headers(admin_user)
    total = _stock_de(client, h_admin, "Tomate")
    assert float(total["entered"]) == 27
    assert float(total["wasted"]) == 2
    assert float(total["on_hand"]) == 25

    solo_obarrio = _stock_de(client, h_admin, "Tomate", branch_id=obarrio_branch.id)
    assert float(solo_obarrio["on_hand"]) == 7


def test_un_agente_solo_ve_las_mermas_de_su_sucursal(client, clayton_branch, obarrio_branch,
                                                     clayton_agent, clayton_device,
                                                     obarrio_agent, obarrio_device, admin_user):
    h_cla = _headers(clayton_agent, clayton_device)
    h_oba = _headers(obarrio_agent, obarrio_device)
    tomate = _item(client, h_cla, "Tomate")

    _waste(client, h_cla, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "1"}])
    _waste(client, h_oba, obarrio_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "1"}])

    mias = client.get("/api/inventory/waste", headers=h_cla).json()
    assert {m["branch_name"] for m in mias} == {"Clayton"}

    todas = client.get("/api/inventory/waste", headers=_headers(admin_user)).json()
    assert {m["branch_name"] for m in todas} == {"Clayton", "Obarrio"}


# ==========================================================================
# Listado
# ==========================================================================
def test_la_lista_de_mermas_trae_la_etiqueta_del_motivo(client, clayton_branch, clayton_agent,
                                                        clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _waste(client, headers, clayton_branch.id,
           [{"inventory_item_id": tomate["id"], "quantity": "1"}], reason="consumo_interno")

    registro = client.get("/api/inventory/waste", headers=headers).json()[0]
    assert registro["reason"] == "consumo_interno"
    assert registro["reason_label"] == "Consumo interno"


def test_la_lista_se_puede_filtrar_por_motivo(client, clayton_branch, clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _waste(client, headers, clayton_branch.id,
           [{"inventory_item_id": tomate["id"], "quantity": "1"}], reason="vencido")
    _waste(client, headers, clayton_branch.id,
           [{"inventory_item_id": tomate["id"], "quantity": "2"}], reason="derrame")

    solo = client.get("/api/inventory/waste?reason=derrame", headers=headers).json()
    assert len(solo) == 1
    assert float(solo[0]["items"][0]["quantity"]) == 2


def test_only_stocked_deja_fuera_lo_que_nunca_se_movio(client, clayton_branch, clayton_agent,
                                                       clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _item(client, headers, "Cebolla")   # existe en el catálogo pero nunca entró ni salió

    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "3"}])

    completo = client.get("/api/inventory/stock", headers=headers).json()
    assert {f["item_name"] for f in completo} == {"Tomate", "Cebolla"}

    movidos = client.get("/api/inventory/stock?only_stocked=true", headers=headers).json()
    assert {f["item_name"] for f in movidos} == {"Tomate"}


def test_la_existencia_registra_cuando_fue_el_ultimo_movimiento(client, clayton_branch,
                                                                clayton_agent, clayton_device):
    headers = _headers(clayton_agent, clayton_device)
    tomate = _item(client, headers, "Tomate")
    _shipment(client, headers, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "3"}])

    fila = _stock_de(client, headers, "Tomate")
    assert fila["last_movement_at"] is not None
