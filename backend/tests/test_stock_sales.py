"""
Existencias que descuentan lo vendido y recetas en otra unidad que el insumo.

  - Lo que "queda hoy" resta lo que se usó en platos vendidos (ventas de Invu × recetas) desde
    el último conteo de cada insumo. Antes nunca se restaba: la existencia solo bajaba con merma.
  - El conteo guarda su diferencia contra los registros solos, así que el análisis descuenta las
    ventas una sola vez y el conteo siguiente arranca limpio.
  - Una receta en gramos de un insumo que se cuenta por pieza se convierte con lo que pesa una
    pieza; sin ese dato el conteo dice "no se pudo calcular" en vez de acusar un faltante.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine
from models.stock_count import StockCount
from routers.inventory import _a_unidad_del_insumo
from tests.conftest import auth_headers_for

_orden = iter(range(500000, 600000))


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _item(client, h, db, name, unit, invu_id=None, ref=None, piece_size=None):
    it = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=h).json()
    row = db.get(InventoryItem, it["id"])
    row.invu_id = invu_id
    row.reference_cost = Decimal(ref) if ref is not None else None
    row.piece_size = Decimal(piece_size) if piece_size is not None else None
    db.commit()
    return it


def _receta(db, branch_id, plato, insumo_invu, cantidad, unidad):
    db.add(InvuRecipeLine(branch_id=branch_id, source_type="item", source_invu_id=plato,
                          product_invu_id=insumo_invu, quantity=Decimal(cantidad), unit_name=unidad))
    db.commit()


def _contar(client, h, branch_id, hace=None, **cantidades):
    res = client.post("/api/inventory/counts", json={
        "branch_id": branch_id,
        "items": [{"inventory_item_id": i, "counted_quantity": str(q)} for i, q in cantidades.items()],
    }, headers=h)
    assert res.status_code == 201, res.text
    body = res.json()
    if hace is not None:
        rec = db_ref["db"].get(StockCount, body["id"])
        rec.counted_at = datetime.now(timezone.utc).replace(tzinfo=None) - hace
        db_ref["db"].commit()
    return body


db_ref: dict = {}


def _vender(db, branch_id, plato, cantidad, cuando):
    venta = InvuSale(branch_id=branch_id, invu_order_id=next(_orden), business_date=cuando.date(),
                     opened_at=cuando, closed_at=cuando, status="Cerrada", total=Decimal("10"))
    db.add(venta)
    db.flush()
    db.add(InvuSaleLine(sale_id=venta.id, branch_id=branch_id, business_date=cuando.date(), invu_line_id=1,
                        invu_item_id=plato, name="Bowl", quantity=Decimal(cantidad), counted=True))
    db.commit()


def _fila(client, h, branch_id, item_id):
    filas = client.get(f"/api/inventory/stock?branch_id={branch_id}", headers=h).json()
    return next(f for f in filas if f["inventory_item_id"] == item_id)


def test_la_existencia_resta_lo_vendido_desde_el_conteo(client, db_session, clayton_branch, supervisor_user, clayton_device):
    db_ref["db"] = db_session
    h = _h(supervisor_user, clayton_device)
    pollo = _item(client, h, db_session, "Pollo", "kg", invu_id=910, ref="5")
    _receta(db_session, clayton_branch.id, 7101, 910, "160", "gramos")   # un bowl lleva 160 g

    # Conteo "de ayer": había 10 kg. Desde entonces, 10 bowls = 1.6 kg.
    primero = _contar(client, h, clayton_branch.id, hace=timedelta(days=1), **{str(pollo["id"]): 10})
    _vender(db_session, clayton_branch.id, 7101, 10, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12))
    # Una venta de antes del conteo no se resta: el conteo ya la absorbió.
    _vender(db_session, clayton_branch.id, 7101, 50, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3))

    f = _fila(client, h, clayton_branch.id, pollo["id"])
    assert Decimal(f["sold_since_count"]) == Decimal("1.6")
    assert Decimal(f["on_hand"]) == Decimal("8.4")

    # Se cuenta lo que tenía que haber: cuadra, y la diferencia se guarda contra los registros.
    segundo = _contar(client, h, clayton_branch.id, hace=timedelta(hours=2), **{str(pollo["id"]): "8.4"})
    linea = segundo["analysis"]["lines"][0]
    assert linea["status"] == "cuadra"
    assert Decimal(linea["used_by_sales"]) == Decimal("1.6")     # descontado una sola vez
    rec = db_session.get(StockCount, segundo["id"])
    assert Decimal(rec.items[0].expected_quantity) == Decimal("10")
    assert Decimal(rec.items[0].difference) == Decimal("-1.6")

    # Recién contado: queda lo contado, sin ventas nuevas que restar.
    f = _fila(client, h, clayton_branch.id, pollo["id"])
    assert f["sold_since_count"] is None and Decimal(f["on_hand"]) == Decimal("8.4")

    # Una venta después del segundo conteo se resta desde ahí.
    _vender(db_session, clayton_branch.id, 7101, 5, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    f = _fila(client, h, clayton_branch.id, pollo["id"])
    assert Decimal(f["sold_since_count"]) == Decimal("0.8") and Decimal(f["on_hand"]) == Decimal("7.6")
    assert primero["id"] != segundo["id"]


def test_sin_conteo_no_se_resta_nada(client, db_session, clayton_branch, supervisor_user, clayton_device):
    db_ref["db"] = db_session
    h = _h(supervisor_user, clayton_device)
    arroz = _item(client, h, db_session, "Arroz", "kg", invu_id=911)
    _receta(db_session, clayton_branch.id, 7102, 911, "0.2", "kg")
    res = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id,
        "items": [{"inventory_item_id": arroz["id"], "quantity": "20"}],
    }, headers=h)
    assert res.status_code == 201, res.text
    _vender(db_session, clayton_branch.id, 7102, 10, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))

    # Nunca se contó: sin punto de partida no se sabe cuánto había cuando empezaron las ventas.
    f = _fila(client, h, clayton_branch.id, arroz["id"])
    assert f["sold_since_count"] is None and Decimal(f["on_hand"]) == Decimal("20")


def test_la_merma_avisa_contra_lo_que_hay_de_verdad(client, db_session, clayton_branch, supervisor_user, clayton_device):
    db_ref["db"] = db_session
    h = _h(supervisor_user, clayton_device)
    pollo = _item(client, h, db_session, "Pollo merma", "kg", invu_id=912, ref="5")
    _receta(db_session, clayton_branch.id, 7103, 912, "1", "kg")
    _contar(client, h, clayton_branch.id, hace=timedelta(days=1), **{str(pollo["id"]): 3})
    _vender(db_session, clayton_branch.id, 7103, 2, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3))   # quedan 1 kg

    res = client.post("/api/inventory/waste", json={
        "branch_id": clayton_branch.id, "reason": "vencido",
        "items": [{"inventory_item_id": pollo["id"], "quantity": "2"}],
    }, headers=h)
    assert res.status_code == 201, res.text
    assert Decimal(res.json()["items"][0]["stock_before"]) == Decimal("1")


def test_receta_en_gramos_de_un_insumo_por_pieza(client, db_session, clayton_branch, supervisor_user, clayton_device):
    db_ref["db"] = db_session
    h = _h(supervisor_user, clayton_device)
    tomate = _item(client, h, db_session, "Tomate", "unidad", invu_id=913, ref="0.30")
    _receta(db_session, clayton_branch.id, 7104, 913, "50", "gramos")   # medio tomate por plato
    _contar(client, h, clayton_branch.id, hace=timedelta(days=1), **{str(tomate["id"]): 20})
    _vender(db_session, clayton_branch.id, 7104, 10, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5))

    # Sin saber cuánto pesa un tomate no se puede convertir: no se acusa un faltante.
    sin_peso = _contar(client, h, clayton_branch.id, **{str(tomate["id"]): 15})
    linea = sin_peso["analysis"]["lines"][0]
    assert linea["status"] == "sin_conversion"
    assert sin_peso["analysis"]["totals"]["no_conversion"] == 1
    assert sin_peso["analysis"]["totals"]["missing"] == 0

    # Con el peso de la pieza (100 g), 10 platos × 50 g = 5 tomates: 20 − 5 = 15, cuadra.
    db_session.get(InventoryItem, tomate["id"]).piece_size = Decimal("100")
    db_session.commit()
    otra = client.get(f"/api/inventory/counts/{sin_peso['id']}/analysis", headers=h).json()
    linea = otra["lines"][0]
    assert linea["status"] == "cuadra"
    assert Decimal(linea["used_by_sales"]) == Decimal("5")


def test_conversion_pieza_contra_peso_y_volumen():
    # Receta en gramos, insumo por unidad (una pieza pesa 150 g).
    assert _a_unidad_del_insumo(Decimal("300"), "g", "unidad", Decimal("150")) == Decimal("2")
    assert _a_unidad_del_insumo(Decimal("0.3"), "kg", "und", Decimal("150")) == Decimal("2")
    # Receta por unidad, insumo en kilos o litros.
    assert _a_unidad_del_insumo(Decimal("2"), "unidad", "kg", Decimal("150")) == Decimal("0.3")
    assert _a_unidad_del_insumo(Decimal("1"), "unidad", "l", Decimal("250")) == Decimal("0.25")
    # Receta en ml, insumo por unidad (una botella trae 150 ml).
    assert _a_unidad_del_insumo(Decimal("300"), "ml", "unidad", Decimal("150")) == Decimal("2")
    # Sin peso por pieza, o en familias que no se cruzan, no se inventa.
    assert _a_unidad_del_insumo(Decimal("300"), "g", "unidad") is None
    assert _a_unidad_del_insumo(Decimal("300"), "g", "ml") is None   # sin gramos por ml
    assert _a_unidad_del_insumo(Decimal("1"), "porcion", "kg", Decimal("150")) is None
    # Lo de siempre sigue igual.
    assert _a_unidad_del_insumo(Decimal("160"), "gramos", "kg") == Decimal("0.16")
