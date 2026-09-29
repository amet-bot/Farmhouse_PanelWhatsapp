"""
Recibir cargamentos contra factura: lo facturado contra lo que llegó, la incidencia y el aviso
automáticos, los cargamentos agendados, la foto de la factura y qué proveedor falla más.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

from models.inventory_item import InventoryItem
from models.ops import Incident
from models.shipment import ExpectedShipment, Shipment
from routers import inventory as inv_router
from routers import receiving as recv_router
from services import invu_sales_sync
from tests.conftest import auth_headers_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def avisos(monkeypatch):
    """Los avisos por push se anotan en vez de salir (y sin abrir la base real en segundo plano)."""
    enviados = []
    monkeypatch.setattr(inv_router, "_avisar_diferencias_background",
                        lambda branch_id, title, body, url, tag: enviados.append(("diferencias", branch_id, title, body)))
    monkeypatch.setattr(recv_router, "_avisar_agendado_background",
                        lambda branch_id, title, body, tag: enviados.append(("agendado", branch_id, title, body)))
    return enviados


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, name, unit):
    return client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()


def _proveedor(client, h, name="PriceSmart"):
    res = client.post("/api/inventory/suppliers", json={"name": name}, headers=h)
    assert res.status_code in (200, 201), res.text
    return res.json()


def _stock(client, h, branch_id, item_id):
    filas = client.get(f"/api/inventory/stock?branch_id={branch_id}", headers=h).json()
    return Decimal(next(f for f in filas if f["inventory_item_id"] == item_id)["on_hand"])


def test_recibir_contra_factura_marca_diferencias_y_avisa(client, db_session, clayton_branch, supervisor_user,
                                                          clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    tomate = _item(client, h, "Tomate", "kg")
    queso = _item(client, h, "Queso", "kg")
    lechuga = _item(client, h, "Lechuga", "unidad")
    prov = _proveedor(client, h)

    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "supplier_id": prov["id"], "invoice_number": "F-1234",
        "items": [
            {"inventory_item_id": tomate["id"], "invoiced_quantity": "10", "quantity": "8", "unit_cost": "1"},
            {"inventory_item_id": queso["id"], "invoiced_quantity": "5", "quantity": "5", "unit_cost": "8"},
            {"inventory_item_id": lechuga["id"], "invoiced_quantity": "4", "quantity": "0", "unit_cost": "0.5",
             "line_status": "equivocado", "line_note": "Llegó repollo"},
        ],
    }, headers=h)
    assert res.status_code == 201, res.text
    body = res.json()
    estados = {l["item_name"]: l["line_status"] for l in body["items"]}
    assert estados == {"Tomate": "falto", "Queso": "ok", "Lechuga": "equivocado"}
    assert body["has_issues"] is True and body["issues_count"] == 2
    assert body["invoice_number"] == "F-1234"
    assert Decimal(body["claim_total"]) == Decimal("4.00")     # 2 kg × $1 + 4 u × $0.50

    # Se abrió una incidencia sola, alta porque llegó un producto equivocado.
    incidencia = db_session.get(Incident, body["incident_id"])
    assert incidencia.severity == "alta" and incidencia.branch_id == clayton_branch.id
    assert "PriceSmart" in incidencia.title
    assert "Tomate: facturado 10 kg, llegó 8 kg (faltó 2 kg)" in incidencia.description
    assert "Llegó repollo" in incidencia.description and "F-1234" in incidencia.description

    # Y se avisó a los encargados.
    assert [a[0] for a in avisos] == ["diferencias"]
    assert "Tomate" in avisos[0][3]

    # La existencia suma lo que llegó, no lo facturado.
    assert _stock(client, h, clayton_branch.id, tomate["id"]) == Decimal("8")
    assert _stock(client, h, clayton_branch.id, lechuga["id"]) == Decimal("0")


def test_sin_factura_es_como_siempre(client, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, "Arroz", "kg")
    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "items": [{"inventory_item_id": arroz["id"], "quantity": "20"}],
    }, headers=h)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["has_issues"] is None and body["incident_id"] is None
    assert body["items"][0]["line_status"] is None
    assert avisos == []


def test_todo_bien_contra_factura_no_abre_incidencia(client, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, "Arroz", "kg")
    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "invoice_number": "F-9",
        "items": [{"inventory_item_id": arroz["id"], "invoiced_quantity": "20", "quantity": "20"}],
    }, headers=h)
    body = res.json()
    assert body["has_issues"] is False and body["incident_id"] is None
    assert body["items"][0]["line_status"] == "ok"
    assert avisos == []


def test_validaciones_de_los_renglones(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, "Arroz", "kg")
    base = {"branch_id": clayton_branch.id}
    cero = client.post("/api/inventory/shipments", json={**base, "items": [{"inventory_item_id": arroz["id"], "quantity": "0"}]}, headers=h)
    assert cero.status_code == 400 and "no puede ser cero" in cero.json()["detail"]
    sin_factura = client.post("/api/inventory/shipments", json={**base, "items": [
        {"inventory_item_id": arroz["id"], "quantity": "3", "line_status": "falto"}]}, headers=h)
    assert sin_factura.status_code == 400 and "factura" in sin_factura.json()["detail"]
    raro = client.post("/api/inventory/shipments", json={**base, "items": [
        {"inventory_item_id": arroz["id"], "quantity": "3", "line_status": "perdido"}]}, headers=h)
    assert raro.status_code == 400
    # No llegó nada de lo facturado: vale, es un faltante completo.
    nada = client.post("/api/inventory/shipments", json={**base, "items": [
        {"inventory_item_id": arroz["id"], "quantity": "0", "invoiced_quantity": "5"}]}, headers=h)
    assert nada.status_code == 201 and nada.json()["items"][0]["line_status"] == "falto"


def test_cargamento_agendado_se_recibe_una_vez(client, db_session, clayton_branch, supervisor_user, clayton_device,
                                               clayton_agent, avisos):
    h = _h(supervisor_user, clayton_device)
    prov = _proveedor(client, h)
    manana = invu_sales_sync.hoy_panama() + timedelta(days=1)

    # La cocina no agenda; el supervisor sí, y la sucursal recibe el aviso.
    agente = client.post("/api/inventory/expected-shipments", json={
        "branch_id": clayton_branch.id, "expected_date": manana.isoformat()}, headers=_h(clayton_agent, clayton_device))
    assert agente.status_code == 403
    res = client.post("/api/inventory/expected-shipments", json={
        "branch_id": clayton_branch.id, "supplier_id": prov["id"], "expected_date": manana.isoformat(),
        "time_from": "15:00", "notes": "Revisar todo con quien entrega",
    }, headers=h)
    assert res.status_code == 201, res.text
    agendado = res.json()
    assert agendado["status"] == "pendiente" and agendado["supplier_name"] == "PriceSmart"
    assert avisos[-1][0] == "agendado" and avisos[-1][2] == "Mañana llega PriceSmart"
    assert "3:00 pm" in avisos[-1][3]

    pendientes = client.get(f"/api/inventory/expected-shipments?branch_id={clayton_branch.id}",
                            headers=_h(clayton_agent, clayton_device)).json()
    assert [p["id"] for p in pendientes] == [agendado["id"]]

    arroz = _item(client, h, "Arroz", "kg")
    recibir = {"branch_id": clayton_branch.id, "supplier_id": prov["id"], "expected_shipment_id": agendado["id"],
               "items": [{"inventory_item_id": arroz["id"], "quantity": "10"}]}
    res = client.post("/api/inventory/shipments", json=recibir, headers=h)
    assert res.status_code == 201, res.text
    envio = res.json()
    assert envio["expected_shipment_id"] == agendado["id"]
    assert db_session.get(ExpectedShipment, agendado["id"]).status == "recibido"
    assert client.post("/api/inventory/shipments", json=recibir, headers=h).status_code == 409

    # Si el cargamento se borra, lo agendado vuelve a esperarse.
    assert client.delete(f"/api/inventory/shipments/{envio['id']}", headers=h).status_code == 204
    db_session.expire_all()
    e = db_session.get(ExpectedShipment, agendado["id"])
    assert e.status == "pendiente" and e.shipment_id is None

    # Y se puede cancelar.
    cancelado = client.post(f"/api/inventory/expected-shipments/{agendado['id']}/cancel", headers=h)
    assert cancelado.status_code == 200 and cancelado.json()["status"] == "cancelado"


def test_no_se_agenda_en_el_pasado(client, clayton_branch, supervisor_user, clayton_device):
    ayer = invu_sales_sync.hoy_panama() - timedelta(days=1)
    res = client.post("/api/inventory/expected-shipments", json={
        "branch_id": clayton_branch.id, "expected_date": ayer.isoformat()}, headers=_h(supervisor_user, clayton_device))
    assert res.status_code == 400


def test_foto_de_la_factura(client, clayton_branch, supervisor_user, clayton_device, obarrio_agent, obarrio_device):
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, "Arroz", "kg")
    envio = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "items": [{"inventory_item_id": arroz["id"], "quantity": "5"}]}, headers=h).json()
    res = client.post(f"/api/inventory/shipments/{envio['id']}/photos",
                      files={"file": ("factura.png", PNG, "image/png")}, headers=h)
    assert res.status_code == 201, res.text
    foto = res.json()["photos"][0]
    ver = client.get(f"/api/inventory/shipments/{envio['id']}/photos/{foto['id']}", headers=h)
    assert ver.status_code == 200 and ver.content == PNG
    # Otra sucursal no la ve; un archivo que no es foto no entra.
    otra = client.get(f"/api/inventory/shipments/{envio['id']}/photos/{foto['id']}", headers=_h(obarrio_agent, obarrio_device))
    assert otra.status_code == 403
    texto = client.post(f"/api/inventory/shipments/{envio['id']}/photos",
                        files={"file": ("x.png", b"hola", "image/png")}, headers=h)
    assert texto.status_code == 415


def test_que_proveedor_falla_mas(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    tomate = _item(client, h, "Tomate", "kg")
    malo = _proveedor(client, h, "Distribuidora X")
    bueno = _proveedor(client, h, "Finca Y")

    def recibir(prov, facturado, llego):
        return client.post("/api/inventory/shipments", json={
            "branch_id": clayton_branch.id, "supplier_id": prov["id"],
            "items": [{"inventory_item_id": tomate["id"], "invoiced_quantity": facturado, "quantity": llego, "unit_cost": "2"}],
        }, headers=h).json()

    recibir(malo, "10", "7")
    recibir(malo, "10", "10")
    recibir(bueno, "5", "5")
    ultimo = recibir(malo, "10", "9")

    filas = client.get(f"/api/inventory/suppliers/issues?branch_id={clayton_branch.id}", headers=h).json()
    assert filas[0]["supplier_name"] == "Distribuidora X"
    assert (filas[0]["shipments"], filas[0]["with_issues"]) == (3, 2)
    assert Decimal(filas[0]["claim_value"]) == Decimal("8.00")     # (3 + 1) kg × $2
    assert filas[1]["supplier_name"] == "Finca Y" and filas[1]["with_issues"] == 0

    # El análisis del cargamento trae el historial del proveedor.
    ins = ultimo["insights"]
    assert (ins["supplier_shipments_90d"], ins["supplier_issues_90d"]) == (3, 2)
    assert Decimal(ins["supplier_claim_90d"]) == Decimal("8.00")
