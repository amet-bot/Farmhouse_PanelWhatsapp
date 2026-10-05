"""
Costo real del Excel de costeo: va en su propia columna, manda sobre el de Invu al valuar la merma,
y el script que lo carga no escribe nada si no se le pide (--apply).
"""
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from models.inventory_item import InventoryItem
from tests.conftest import auth_headers_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import load_costeo  # noqa: E402


@pytest.fixture
def item(db_session):
    i = InventoryItem(name="Aceite", unit="gramos", code="P22", reference_cost=Decimal("0.0100"))
    db_session.add(i); db_session.commit(); db_session.refresh(i)
    return i


def test_effective_cost_prefiere_el_del_excel_y_cae_al_de_invu(db_session, item):
    assert item.effective_cost == Decimal("0.0100")
    item.costing_cost = Decimal("0.0158"); db_session.commit()
    assert item.effective_cost == Decimal("0.0158")
    # También en SQL (los reportes lo usan dentro de un coalesce).
    assert db_session.query(InventoryItem.effective_cost).filter(InventoryItem.id == item.id).scalar() == Decimal("0.0158")


def test_la_merma_se_valua_con_el_costo_del_excel(client, db_session, clayton_branch, clayton_agent, clayton_device, item):
    item.costing_cost = Decimal("0.0200"); db_session.commit()
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    r = client.post("/api/inventory/waste", json={"branch_id": clayton_branch.id, "reason": "vencido",
                    "items": [{"inventory_item_id": item.id, "quantity": "100"}]}, headers=h)
    assert r.status_code == 201, r.text
    fila = client.get(f"/api/inventory/stock?branch_id={clayton_branch.id}", headers=h).json()[0]
    assert Decimal(fila["wasted_cost"]) == Decimal("2.00")   # 100 g × 0.02, no × 0.01 de Invu


def test_costo_por_unidad_del_script():
    assert load_costeo.costo_por_unidad({"cost_g": 0.015789}, "gramos")[0] == Decimal("0.015789")
    assert load_costeo.costo_por_unidad({"cost_g": 0.01682}, "kilogramo")[0] == Decimal("16.82")
    assert load_costeo.costo_por_unidad({"cost_unit": 0.17}, "unidad")[0] == Decimal("0.17")
    assert load_costeo.costo_por_unidad({"cost_g": None}, "gramos")[0] is None
    assert load_costeo.costo_por_unidad({"cost_g": 1}, "Mazo")[0] is None
