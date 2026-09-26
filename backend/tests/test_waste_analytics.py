"""
Análisis de merma: que cada número salga de donde dice que sale.

- costo: el guardado con la merma (último cargamento de la sucursal) o, si no hay, el de
  referencia de Invu, informado aparte como estimado;
- kilos: la cantidad si el insumo va en peso, o el peso de balanza si la merma tiene un solo
  insumo que va por unidad;
- días en hora de Panamá; % sobre la venta neta de Invu del mismo período.
"""
from datetime import date
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuSyncDay
from tests.conftest import auth_headers_for

PERIODO = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, name, unit):
    return client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()


def _merma(client, h, branch_id, lineas, when="2026-03-10T15:00:00Z", reason="vencido", **extra):
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": reason, "occurred_at": when, "items": lineas, **extra,
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def _analisis(client, h, **params):
    res = client.get("/api/inventory/waste/analytics", params={**PERIODO, **params}, headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def test_costos_kilos_y_porcentaje_de_venta(client, db_session, clayton_branch, admin_user):
    h = _h(admin_user)
    tomate = _item(client, h, "Tomate", "kg")
    pina = _item(client, h, "Piña", "unidad")
    queso = _item(client, h, "Queso Feta", "gramos")
    db_session.get(InventoryItem, pina["id"]).reference_cost = Decimal("1.25")
    db_session.commit()

    # El tomate tiene costo real: un cargamento de Clayton a $2.00/kg.
    client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id, "received_at": "2026-03-01T12:00:00Z",
        "items": [{"inventory_item_id": tomate["id"], "quantity": "10", "unit_cost": "2.00"}],
    }, headers=h)

    _merma(client, h, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "3"}])
    _merma(client, h, clayton_branch.id, [{"inventory_item_id": pina["id"], "quantity": "2"}],
           weight_value="1.35", weight_unit="kg")
    _merma(client, h, clayton_branch.id, [{"inventory_item_id": queso["id"], "quantity": "500"}])

    # Venta neta de Invu en marzo para Clayton: $100.
    db_session.add(InvuSyncDay(branch_id=clayton_branch.id, business_date=date(2026, 3, 10), net_total=Decimal("100.00")))
    db_session.commit()

    a = _analisis(client, h)
    t = a["totals"]
    assert t["records"] == 3
    assert Decimal(t["cost_total"]) == Decimal("8.50")        # 3 kg × $2 + 2 piñas × $1.25 (Invu)
    assert Decimal(t["cost_estimated"]) == Decimal("2.50")
    assert t["lines_without_cost"] == 1                        # el queso: sin cargamento ni costo de Invu
    assert Decimal(t["kg_total"]) == Decimal("4.850")          # 3 + 1.35 (balanza) + 0.5 (500 g)
    assert t["records_with_weight"] == 1
    assert Decimal(t["sales_net"]) == Decimal("100.00")
    assert Decimal(t["waste_pct_of_sales"]) == Decimal("8.50")

    por_item = {i["name"]: i for i in a["by_item"]}
    assert por_item["Tomate"]["cost"] == "6.00" and por_item["Tomate"]["estimated"] is False
    assert por_item["Piña"]["kg"] == "1.350" and por_item["Piña"]["estimated"] is True
    assert por_item["Queso Feta"]["kg"] == "0.500"
    assert a["by_item"][0]["name"] == "Tomate"                 # ordenado por plata perdida

    marzo10 = next(d for d in a["by_day"] if d["date"] == "2026-03-10")
    assert marzo10["records"] == 3 and Decimal(marzo10["cost"]) == Decimal("8.50")
    assert len(a["by_day"]) == 31

    suc = a["by_branch"][0]
    assert suc["label"] == clayton_branch.name and Decimal(suc["waste_pct_of_sales"]) == Decimal("8.50")


def test_peso_de_balanza_no_se_reparte_entre_varios_insumos(client, clayton_branch, admin_user):
    h = _h(admin_user)
    pina = _item(client, h, "Piña", "unidad")
    mamey = _item(client, h, "Mamey", "unidad")
    _merma(client, h, clayton_branch.id, [
        {"inventory_item_id": pina["id"], "quantity": "1"},
        {"inventory_item_id": mamey["id"], "quantity": "1"},
    ], weight_value="2", weight_unit="kg")
    t = _analisis(client, h)["totals"]
    assert Decimal(t["kg_total"]) == Decimal("0") and t["lines_without_kg"] == 2


def test_el_dia_es_el_de_panama(client, clayton_branch, admin_user):
    h = _h(admin_user)
    tomate = _item(client, h, "Tomate", "kg")
    # 03:00 UTC del 11 = 22:00 del 10 en Panamá.
    _merma(client, h, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "1"}], when="2026-03-11T03:00:00Z")
    dias = {d["date"]: d["records"] for d in _analisis(client, h)["by_day"]}
    assert dias["2026-03-10"] == 1 and dias["2026-03-11"] == 0


def test_cada_sucursal_ve_la_suya(client, clayton_branch, obarrio_branch, clayton_agent, clayton_device,
                                   obarrio_agent, obarrio_device):
    hc = _h(clayton_agent, clayton_device)
    tomate = _item(client, hc, "Tomate", "kg")
    _merma(client, hc, clayton_branch.id, [{"inventory_item_id": tomate["id"], "quantity": "1"}])
    ho = _h(obarrio_agent, obarrio_device)
    # Aunque pida Clayton, ve la suya (Obarrio), que no tiene mermas.
    a = _analisis(client, ho, branch_id=clayton_branch.id)
    assert a["branch_id"] == obarrio_branch.id and a["totals"]["records"] == 0


def test_periodo_invalido(client, admin_user):
    h = _h(admin_user)
    assert client.get("/api/inventory/waste/analytics", params={"date_from": "2026-03-10", "date_to": "2026-03-01"}, headers=h).status_code == 400
    assert client.get("/api/inventory/waste/analytics", params={"date_from": "2024-01-01", "date_to": "2026-03-01"}, headers=h).status_code == 400


def test_existencias_valuan_la_merma_con_invu_si_no_hay_cargamento(client, db_session, clayton_branch, admin_user):
    h = _h(admin_user)
    pina = _item(client, h, "Piña", "unidad")
    db_session.get(InventoryItem, pina["id"]).reference_cost = Decimal("1.25")
    db_session.commit()
    _merma(client, h, clayton_branch.id, [{"inventory_item_id": pina["id"], "quantity": "2"}])

    filas = client.get("/api/inventory/stock", params={"branch_id": clayton_branch.id, "only_stocked": True}, headers=h).json()
    fila = next(f for f in filas if f["inventory_item_id"] == pina["id"])
    assert Decimal(fila["wasted_cost"]) == Decimal("2.50") and fila["wasted_cost_estimated"] is True
    assert Decimal(fila["on_hand"]) == Decimal("-2")


def test_recorte_es_merma_de_proceso_con_rendimiento(client, db_session, clayton_branch, admin_user):
    h = _h(admin_user)
    pollo = _item(client, h, "Pechuga de pollo", "kilogramo")
    db_session.get(InventoryItem, pollo["id"]).reference_cost = Decimal("6.00")
    db_session.commit()

    # De 5 kg de pollo limpiado quedaron 0.270 kg de recorte (el ejemplo de la balanza).
    rec = _merma(client, h, clayton_branch.id, [{"inventory_item_id": pollo["id"], "quantity": "0.270"}],
                 reason="recorte", weight_value="0.270", weight_unit="kg", processed_value="5", processed_unit="kg")
    assert rec["is_process"] is True and rec["reason_label"] == "Recorte o limpieza"
    assert Decimal(rec["processed_value"]) == Decimal("5") and Decimal(rec["yield_pct"]) == Decimal("94.6")

    # Un vencido es evitable; lo limpiado se ignora fuera de los recortes.
    otro = _merma(client, h, clayton_branch.id, [{"inventory_item_id": pollo["id"], "quantity": "0.5"}],
                  processed_value="3")
    assert otro["is_process"] is False and otro["processed_value"] is None and otro["yield_pct"] is None

    a = _analisis(client, h)
    t = a["totals"]
    assert Decimal(t["cost_process"]) == Decimal("1.62")      # 0.270 kg × $6
    assert Decimal(t["kg_process"]) == Decimal("0.270")
    naturaleza = {g["key"]: g for g in a["by_nature"]}
    assert Decimal(naturaleza["proceso"]["cost"]) == Decimal("1.62")
    assert Decimal(naturaleza["evitable"]["cost"]) == Decimal("3.00")
    y = a["yields"][0]
    assert y["name"] == "Pechuga de pollo" and Decimal(y["yield_pct"]) == Decimal("94.6")
    assert Decimal(y["processed_kg"]) == Decimal("5.000") and Decimal(y["trimmed_kg"]) == Decimal("0.270")


def test_rendimiento_sin_sentido_no_se_informa(client, clayton_branch, admin_user):
    h = _h(admin_user)
    pollo = _item(client, h, "Pollo", "kilogramo")
    # Más recorte que lo limpiado (un error de tipeo): no se inventa un rendimiento negativo.
    rec = _merma(client, h, clayton_branch.id, [{"inventory_item_id": pollo["id"], "quantity": "2"}],
                 reason="recorte", processed_value="1", processed_unit="kg")
    assert rec["yield_pct"] is None
    assert _analisis(client, h)["yields"] == []


def test_la_merma_muestra_su_costo_estimado_con_invu(client, db_session, clayton_branch, admin_user):
    """Sin cargamento con costo, la lista y el detalle muestran el estimado de Invu (≈), no "—"."""
    h = _h(admin_user)
    aguacate = _item(client, h, "Aguacate", "gramos")
    db_session.get(InventoryItem, aguacate["id"]).reference_cost = Decimal("0.0066")
    db_session.commit()
    w = _merma(client, h, clayton_branch.id, [{"inventory_item_id": aguacate["id"], "quantity": "850"}])
    assert w["total_cost"] is None                     # costo real: no hay cargamento
    assert Decimal(w["display_cost"]) == Decimal("5.61") and w["cost_estimated"] is True
    assert Decimal(w["items"][0]["reference_cost"]) == Decimal("0.0066")
