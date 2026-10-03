"""
Merma rápida (/merma): la lista de insumos para la tablet (los más botados primero), las fotos
de los insumos y que registrar con lo que manda la pantalla funcione igual que la merma de siempre.
"""
from datetime import datetime, timezone
from decimal import Decimal

from models.audit import AuditEvent
from models.inventory_item import InventoryItem
from models.waste import WasteItem, WasteRecord
from tests.conftest import auth_headers_for

JPG = b"\xff\xd8\xff\xe0" + b"0" * 200


def _catalogo(db):
    palta = InventoryItem(name="Aguacate", unit="gramos", category="Vegetales")
    pan = InventoryItem(name="Pan multigrano", unit="unidad", category="Panaderia")
    leche = InventoryItem(name="Leche", unit="litros", category="Lacteos")
    viejo = InventoryItem(name="Insumo viejo", unit="kg", active=False)
    db.add_all([palta, pan, leche, viejo]); db.commit()
    return palta, pan, leche


def test_lista_para_la_tablet(client, db_session, clayton_agent, clayton_device, clayton_branch, obarrio_branch, admin_user):
    palta, pan, leche = _catalogo(db_session)
    rec = WasteRecord(branch_id=clayton_branch.id, recorded_by_user_id=clayton_agent.id, occurred_at=datetime.now(timezone.utc), reason="vencido")
    rec.items = [WasteItem(inventory_item_id=pan.id, quantity=Decimal("2"))]
    db_session.add(rec); db_session.commit()

    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    d = client.get(f"/api/quick-waste/items?branch_id={obarrio_branch.id}", headers=h).json()
    # El agente queda en su sucursal aunque pida otra, y no recibe la lista para elegir.
    assert d["branch"]["id"] == clayton_branch.id and d["branches"] == [] and d["can_edit_photos"] is False
    nombres = [i["name"] for i in d["items"]]
    assert nombres[0] == "Pan multigrano" and "Insumo viejo" not in nombres   # lo más botado primero; inactivos fuera
    por = {i["name"]: i for i in d["items"]}
    assert por["Aguacate"]["family"] == "peso" and float(por["Aguacate"]["unit_base"]) == 1
    assert por["Leche"]["family"] == "volumen" and float(por["Leche"]["unit_base"]) == 1000
    assert por["Pan multigrano"]["family"] == "unidad" and por["Pan multigrano"]["times"] == 1
    assert [r["code"] for r in d["reasons"]][:2] == ["vencido", "danado"]

    # Admin: elige sucursal; sin pedir una, la primera.
    da = client.get("/api/quick-waste/items", headers=auth_headers_for(admin_user)).json()
    assert len(da["branches"]) == 2 and da["branch"]["id"] in {clayton_branch.id, obarrio_branch.id}
    assert da["can_edit_photos"] is True


def test_fotos_de_insumos(client, db_session, clayton_agent, clayton_device, supervisor_user, admin_user):
    palta, _pan, _leche = _catalogo(db_session)
    hag = auth_headers_for(clayton_agent, clayton_device.device_id)
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    url = f"/api/quick-waste/items/{palta.id}/photo"
    assert client.get(url, headers=hag).status_code == 404
    # Un agente no cambia fotos; un encargado sí. Solo imágenes de verdad.
    assert client.put(url, files={"file": ("a.jpg", JPG, "image/jpeg")}, headers=hag).status_code == 403
    assert client.put(url, files={"file": ("a.jpg", b"no es foto", "image/jpeg")}, headers=hs).status_code == 415
    r = client.put(url, files={"file": ("a.jpg", JPG, "image/jpeg")}, headers=hs)
    assert r.status_code == 200 and r.json()["photo_v"]
    g = client.get(url, headers=hag)
    assert g.status_code == 200 and g.content == JPG and g.headers["content-type"] == "image/jpeg"
    por = {i["name"]: i for i in client.get("/api/quick-waste/items", headers=hag).json()["items"]}
    assert por["Aguacate"]["photo_v"] is not None and por["Leche"]["photo_v"] is None
    # Cambiarla reemplaza (una por insumo) y queda en auditoría.
    client.put(url, files={"file": ("b.jpg", JPG + b"1", "image/jpeg")}, headers=hs)
    assert client.get(url, headers=hag).content == JPG + b"1"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "item.photo").count() == 2
    assert client.delete(url, headers=auth_headers_for(admin_user)).status_code == 204
    assert client.get(url, headers=hag).status_code == 404


def test_registrar_como_lo_manda_la_pantalla(client, db_session, clayton_agent, clayton_device, clayton_branch):
    palta, pan, leche = _catalogo(db_session)
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    # Peso en gramos de un insumo en gramos; 2 panes enteros; 350 ml de leche (insumo en litros).
    for linea, extra in (
        ({"inventory_item_id": palta.id, "quantity": "350"}, {"weight_value": "350", "weight_unit": "g"}),
        ({"inventory_item_id": pan.id, "mode": "entera", "pieces": "2"}, {}),
        ({"inventory_item_id": leche.id, "quantity": "0.35"}, {}),
    ):
        r = client.post("/api/inventory/waste", json={"branch_id": clayton_branch.id, "reason": "vencido", "items": [linea], **extra}, headers=h)
        assert r.status_code == 201, r.text
    cant = {w.inventory_item_id: w.quantity for w in db_session.query(WasteItem).all()}
    assert cant[palta.id] == Decimal("350") and cant[pan.id] == Decimal("2") and cant[leche.id] == Decimal("0.35")
    # Deshacer: quien la registró la borra enseguida.
    ultima = db_session.query(WasteRecord).order_by(WasteRecord.id.desc()).first()
    assert client.delete(f"/api/inventory/waste/{ultima.id}", headers=h).status_code == 204


def test_historial_por_dia_y_merma_por_numero(client, db_session, clayton_agent, clayton_device, clayton_branch, obarrio_agent, obarrio_device):
    from datetime import timedelta
    palta, _pan, _leche = _catalogo(db_session)
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    hoy = WasteRecord(branch_id=clayton_branch.id, recorded_by_user_id=clayton_agent.id, occurred_at=ahora, reason="vencido")
    hoy.items = [WasteItem(inventory_item_id=palta.id, quantity=Decimal("100"))]
    vieja = WasteRecord(branch_id=clayton_branch.id, recorded_by_user_id=clayton_agent.id, occurred_at=ahora - timedelta(days=3), reason="danado")
    vieja.items = [WasteItem(inventory_item_id=palta.id, quantity=Decimal("50"))]
    db_session.add_all([hoy, vieja]); db_session.commit()

    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    from services.invu_sales_sync import hoy_panama
    dia = hoy_panama().isoformat()
    filas = client.get(f"/api/inventory/waste?date_from={dia}&date_to={dia}", headers=h).json()
    assert [f["id"] for f in filas] == [hoy.id]
    assert len(client.get("/api/inventory/waste", headers=h).json()) == 2

    # Una por número: la ve quien ve esa sucursal; la de otra sucursal, no.
    assert client.get(f"/api/inventory/waste/{vieja.id}", headers=h).json()["reason"] == "danado"
    assert client.get(f"/api/inventory/waste/{vieja.id}", headers=auth_headers_for(obarrio_agent, obarrio_device.device_id)).status_code in (403, 404)
    # Las rutas con nombre siguen funcionando (no las tapa /waste/{id}).
    assert client.get("/api/inventory/waste/reasons", headers=h).status_code == 200
