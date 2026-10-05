"""
Recetas en otra unidad que el insumo: la receta pide "50 g de agua de pipa" y el insumo se mide en
ml, o pide "1 pan" y el pan se lleva en gramos. Sin el dato para convertir esa línea no se
descuenta; Recetas → Unidades las lista y deja poner cuánto pesa 1 ml o cuánto es 1 pieza.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from models.audit import AuditEvent
from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from routers import inventory as inv
from routers.inventory import _a_unidad_del_insumo
from tests.conftest import auth_headers_for


def test_conversion_entre_gramos_y_mililitros():
    # Sin gramos por ml no se inventa.
    assert _a_unidad_del_insumo(Decimal("50"), "g", "mililitros") is None
    # Como agua: 50 g = 50 ml.
    assert _a_unidad_del_insumo(Decimal("50"), "g", "mililitros", None, Decimal("1")) == Decimal("50")
    # Miel (1.25 g por ml), insumo en litros: 50 g = 40 ml = 0.04 l.
    assert _a_unidad_del_insumo(Decimal("50"), "gramos", "l", None, Decimal("1.25")) == Decimal("0.04")
    # Al revés: receta en ml, insumo en kilos. 100 ml de aceite (0.92) = 92 g = 0.092 kg.
    assert _a_unidad_del_insumo(Decimal("100"), "ml", "kg", None, Decimal("0.92")) == Decimal("0.092")
    # La densidad no cambia lo que ya funcionaba.
    assert _a_unidad_del_insumo(Decimal("160"), "gramos", "kg", None, Decimal("3")) == Decimal("0.16")


def _venta(db, branch, item_id, nombre, cantidad, n):
    hoy = date.today()
    s = InvuSale(branch_id=branch.id, invu_order_id=700 + n, business_date=hoy, status="Cerrada", opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    db.add(s); db.flush()
    db.add(InvuSaleLine(sale_id=s.id, branch_id=branch.id, business_date=hoy, invu_line_id=600 + n, invu_item_id=item_id, name=nombre, quantity=Decimal(cantidad), counted=True))
    db.commit()


def test_unidades_por_resolver_y_densidad(client, db_session, admin_user, supervisor_user, clayton_agent, clayton_device, clayton_branch):
    pipa = InventoryItem(name="Agua de coco", unit="mililitros", invu_id=10)
    pan = InventoryItem(name="Pan multigrano", unit="gramos", invu_id=11)
    db_session.add_all([pipa, pan]); db_session.commit()
    # En Invu: el smoothie pide la pipa en gramos y el sandwich el pan por unidad.
    db_session.add_all([
        InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=300, source_name="Mango",
                       product_invu_id=10, product_name="Agua de coco", quantity=Decimal("200"), unit_name="gramos"),
        InvuRecipeLine(branch_id=clayton_branch.id, source_type="item", source_invu_id=301, source_name="Hot tuna",
                       product_invu_id=11, product_name="Pan multigrano", quantity=Decimal("2"), unit_name="unidad"),
    ])
    db_session.commit()
    _venta(db_session, clayton_branch, 300, "Mango", "3", 1)

    ha = auth_headers_for(admin_user)
    d = client.get("/api/recipes/unit-issues", headers=ha).json()
    por = {f["item"]["name"]: f for f in d["pending"]}
    assert por["Agua de coco"]["problems"] == [{"recipe_unit": "gramos", "kind": "densidad", "dishes_count": 1}]
    assert por["Pan multigrano"]["problems"][0]["kind"] == "pieza"
    assert d["fixable"] == 2 and d["resolved"] == []

    # Sin densidad, la pipa del Mango no suma.
    desde, hasta = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1), datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    assert pipa.id not in inv._uso_por_ventas(db_session, clayton_branch.id, desde, hasta)

    # Un agente no la pone; un encargado sí, y queda en auditoría.
    hag = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert client.patch(f"/api/inventory/items/{pipa.id}/density", json={"grams_per_ml": "1"}, headers=hag).status_code == 403
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    r = client.patch(f"/api/inventory/items/{pipa.id}/density", json={"grams_per_ml": "1"}, headers=hs)
    assert r.status_code == 200 and float(r.json()["grams_per_ml"]) == 1
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "item.grams_per_ml").count() == 1
    assert client.patch(f"/api/inventory/items/{pipa.id}/density", json={"grams_per_ml": "9"}, headers=hs).status_code == 422

    # Ahora suma: 3 mangos x 200 g = 600 ml. Y pasa a "resueltos".
    db_session.expire_all()
    assert inv._uso_por_ventas(db_session, clayton_branch.id, desde, hasta)[pipa.id] == Decimal("600")
    d = client.get("/api/recipes/unit-issues", headers=ha).json()
    assert [f["item"]["name"] for f in d["pending"]] == ["Pan multigrano"]
    assert [f["item"]["name"] for f in d["resolved"]] == ["Agua de coco"]

    # El pan se resuelve con lo que pesa una pieza (el dato que ya existía).
    assert client.patch(f"/api/inventory/items/{pan.id}/piece-size", json={"piece_size": "80"}, headers=hs).status_code == 200
    d = client.get("/api/recipes/unit-issues", headers=ha).json()
    assert d["pending"] == [] and d["fixable"] == 0


def test_receta_cargada_marca_la_linea_sin_unidad(client, db_session, admin_user):
    leche = InventoryItem(name="Leche de almendras", unit="mililitros", invu_id=12)
    db_session.add(leche); db_session.commit()
    ha = auth_headers_for(admin_user)
    payload = {"recipes": [{"name": "Golden milk", "sale_price": "5", "lines": [{"name": "Leche de almendras", "quantity": "250", "unit": "g"},
                                                                               {"name": "Curcuma", "quantity": "2", "unit": "g"}]}]}
    assert client.post("/api/recipes/import", json=payload, headers=ha).status_code == 200
    linea = next(l for l in client.get("/api/recipes", headers=ha).json()["recipes"][0]["lines"] if l["name"] == "Leche de almendras")
    assert linea["status"] == "emparejado" and linea["unit_issue"] is True
    d = client.get("/api/recipes/unit-issues", headers=ha).json()
    assert d["pending"][0]["item"]["name"] == "Leche de almendras" and d["pending"][0]["dishes"] == ["Golden milk"]
    client.patch(f"/api/inventory/items/{leche.id}/density", json={"grams_per_ml": "1.03"}, headers=ha)
    linea = next(l for l in client.get("/api/recipes", headers=ha).json()["recipes"][0]["lines"] if l["name"] == "Leche de almendras")
    assert linea["unit_issue"] is False
