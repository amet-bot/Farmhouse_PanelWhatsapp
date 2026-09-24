"""
Farmhouse Link, etapa 1: ventas de Invu → base local.

Nunca se habla con Invu de verdad: se reemplazan `list_orders`, `order_totals` e
`iter_menu_items`. Las órdenes de prueba copian la forma de las reales (Costa del Este,
23/09/2026), incluida la nota de crédito, que es donde una sincronización así se equivoca:
la devolución vive dos veces — como orden aparte y como líneas "Devuelto NC" en la original.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from config import settings
from models.invu_sales import InvuMenuItem, InvuSale, InvuSaleLine, InvuSaleModifier, InvuSyncDay
from services import invu_client, invu_sales_sync
from tests.conftest import auth_headers_for

DIA = date(2026, 9, 23)


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _linea(linea_id, codigo, nombre, cantidad, total, estado="Agregado", modif=None):
    return {
        "id": linea_id, "id_item": int(codigo[2:]), "codigo": codigo, "desc_item": nombre,
        "categoria": "Bowls", "cantidad": cantidad, "precioSugerido": total / cantidad,
        "desc_status_venta_item": estado, "modif": modif or [],
        "totales_item": {"subtotal_before_discounts": total, "descuento_total": 0, "total": total},
    }


def _orden(orden_id, items, pagada="Cerrada", num_cita=None, apertura="2026-09-23 12:10:20"):
    total = round(sum(i["totales_item"]["total"] for i in items), 2)
    return {
        "id": str(orden_id), "fecha_apertura_date": apertura, "fecha_cierre_date": apertura,
        "pagada": pagada, "num_cita": num_cita or f"1-{orden_id}", "desc_tipo_orden": "Orden Normal",
        "tipo_integracion_desc": "INVUPOS", "items": items,
        "totales": {"subtotal": total, "descuento": 0, "tax": 0, "total": total},
    }


def _dia_con_devolucion():
    """Tres órdenes: una normal, una con dos platos devueltos, y la nota de crédito de esos dos."""
    bowl = _linea(1, "MF364", "BOWL PERSONALIZADO NUEVO", 2, 37.9, modif=[
        # la clave con el acento grave colado es como viene de verdad en algunas rutas
        {"id": 232, "codigo": "MDD232", "nombre": "Salmon Bulgogi", "cantidad_ve`ndida": 2, "total": 12},
    ])
    normal = _orden(69008, [bowl, _linea(2, "MF298", "pretty in pink", 1, 10)])
    con_devolucion = _orden(69028, [
        _linea(3, "MF118", "La Cosecha", 1, 16.95, estado="Devuelto NC"),
        _linea(4, "MF050", "Coca Cola Zero", 1, 2.5),
        _linea(5, "MF120", "El Cesar", 1, 16.95, estado="Devuelto NC"),
    ])
    nota = _orden(69030, [
        _linea(6, "MF118", "La Cosecha", 1, 16.95, estado="Devuelto NC"),
        _linea(7, "MF120", "El Cesar", 1, 16.95, estado="Devuelto NC"),
    ], pagada="Nota Credito", num_cita="NC-/1-69028")
    return [normal, con_devolucion, nota]


# Cerradas 47.9 + 36.4 = 84.30, menos la nota 33.90 = 50.40
TOTAL_NETO = Decimal("50.40")


@pytest.fixture
def invu_ventas(monkeypatch, clayton_branch, obarrio_branch):
    """Clayton y Obarrio con usuario de API; Invu responde lo que cada prueba cargue."""
    for campo in ("CLY", "OBR"):
        monkeypatch.setattr(settings, f"INVU_USER_{campo}", f"api_{campo.lower()}")
        monkeypatch.setattr(settings, f"INVU_PASS_{campo}", "clave")
    for campo in ("CDE", "VP", "SF"):
        monkeypatch.setattr(settings, f"INVU_USER_{campo}", None)
        monkeypatch.setattr(settings, f"INVU_PASS_{campo}", None)
    monkeypatch.setattr(invu_sales_sync, "PAUSA_ENTRE_DIAS", 0)

    estado = {"ordenes": {}, "totales": {}, "menu": {}, "llamadas": []}

    def list_orders(cred, desde, hasta):
        estado["llamadas"].append((cred.username, desde, hasta))
        return estado["ordenes"].get(cred.username, [])

    def order_totals(cred, desde, hasta):
        return estado["totales"].get(cred.username, {})

    def iter_menu_items(cred):
        return iter(estado["menu"].get(cred.username, []))

    monkeypatch.setattr(invu_client, "list_orders", list_orders)
    monkeypatch.setattr(invu_client, "order_totals", order_totals)
    monkeypatch.setattr(invu_client, "iter_menu_items", iter_menu_items)
    return estado


# ==========================================================================
# Configuración
# ==========================================================================
def test_solo_se_sincronizan_las_sucursales_con_credenciales(db_session, invu_ventas):
    codigos = [b.code for b, _ in invu_sales_sync.sucursales_configuradas(db_session)]
    assert codigos == ["CLY", "OBR"]


def test_la_ventana_del_dia_es_en_hora_de_panama():
    desde, hasta = invu_sales_sync.ventana_del_dia(DIA)
    assert datetime.fromtimestamp(desde, timezone.utc) == datetime(2026, 9, 23, 5, 0, tzinfo=timezone.utc)
    assert hasta - desde == 86399


# ==========================================================================
# Un día
# ==========================================================================
def test_un_dia_con_nota_de_credito_cuadra_con_invu(db_session, invu_ventas, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 50.4}

    cred = invu_client.Credenciales("api_cly", "clave")
    registro = invu_sales_sync.sync_day(db_session, clayton_branch, cred, DIA)

    assert registro.orders_count == 3
    assert registro.net_total == TOTAL_NETO
    assert registro.matches is True

    contadas = db_session.query(InvuSaleLine).filter(InvuSaleLine.counted == True).all()
    # Se venden: el bowl (2), el smoothie y la coca; lo devuelto y la nota no cuentan.
    assert sorted(l.code for l in contadas) == ["MF050", "MF298", "MF364"]
    assert sum(l.quantity for l in contadas) == Decimal("4")

    nota = db_session.query(InvuSale).filter(InvuSale.invu_order_id == 69030).one()
    assert nota.is_credit_note is True

    modif = db_session.query(InvuSaleModifier).one()
    assert (modif.name, modif.quantity) == ("Salmon Bulgogi", Decimal("2"))


def test_las_horas_se_guardan_en_utc_y_el_dia_en_hora_local(db_session, invu_ventas, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = [_orden(1, [_linea(1, "MF001", "Tarde", 1, 5)], apertura="2026-09-23 21:30:00")]
    invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)

    venta = db_session.query(InvuSale).one()
    assert venta.business_date == DIA                       # sigue siendo el 23 en Panamá
    assert venta.opened_at == datetime(2026, 9, 24, 2, 30)  # aunque en UTC ya sea el 24


def test_volver_a_sincronizar_no_duplica_y_toma_los_cambios(db_session, invu_ventas, clayton_branch):
    cred = invu_client.Credenciales("api_cly", "clave")
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_sales_sync.sync_day(db_session, clayton_branch, cred, DIA)

    # Al otro día: devolvieron también la coca de la 69028, y la 69008 fue eliminada en Invu.
    ordenes = _dia_con_devolucion()[1:]
    ordenes[0]["items"][1]["desc_status_venta_item"] = "Devuelto NC"
    invu_ventas["ordenes"]["api_cly"] = ordenes
    invu_sales_sync.sync_day(db_session, clayton_branch, cred, DIA)

    assert db_session.query(InvuSale).count() == 2
    assert db_session.query(InvuSaleLine).count() == 5
    assert db_session.query(InvuSaleLine).filter(InvuSaleLine.counted == True).count() == 0
    assert db_session.query(InvuSaleModifier).count() == 0   # se fueron con la orden eliminada
    assert db_session.query(InvuSyncDay).count() == 1


def test_sin_total_de_invu_no_se_afirma_que_cuadra(db_session, invu_ventas, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    registro = invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)
    assert registro.matches is None


def test_total_distinto_queda_marcado(db_session, invu_ventas, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 99.99}
    registro = invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)
    assert registro.matches is False


# ==========================================================================
# Menú
# ==========================================================================
def test_el_menu_desactiva_lo_que_ya_no_viene(db_session, invu_ventas, clayton_branch):
    cred = invu_client.Credenciales("api_cly", "clave")
    invu_ventas["menu"]["api_cly"] = [
        {"id": 320, "code": "MF320", "name": "Açaí Bowl", "suggested_price": "10.00"},
        {"id": 364, "code": "MF364", "name": "BOWL PERSONALIZADO NUEVO", "suggested_price": "13.95"},
    ]
    assert invu_sales_sync.sync_menu(db_session, clayton_branch, cred)["creados"] == 2

    invu_ventas["menu"]["api_cly"] = invu_ventas["menu"]["api_cly"][1:]
    resumen = invu_sales_sync.sync_menu(db_session, clayton_branch, cred)
    assert resumen == {"recibidos": 1, "creados": 0, "desactivados": 1}
    acai = db_session.query(InvuMenuItem).filter(InvuMenuItem.invu_id == 320).one()
    assert acai.active is False and acai.name == "Açaí Bowl"


# ==========================================================================
# Qué días se piden
# ==========================================================================
def test_sin_historial_pide_hoy_ayer_la_semana_y_un_tramo_viejo(db_session, invu_ventas, clayton_branch):
    dias = invu_sales_sync.dias_pendientes(db_session, clayton_branch.id, DIA)
    assert dias[0] == DIA and dias[1] == DIA - timedelta(days=1)
    # hoy + los DIAS_A_REVISAR anteriores + un tramo del historial
    assert len(dias) == 1 + invu_sales_sync.DIAS_A_REVISAR + invu_sales_sync.DIAS_VIEJOS_POR_PASADA
    assert dias == sorted(dias, reverse=True)


def test_los_dias_recientes_se_revisan_una_vez_por_dia(db_session, invu_ventas, clayton_branch):
    ahora = datetime(2026, 9, 23, 20, 0)
    for atras in range(0, invu_sales_sync.DIAS_DE_HISTORIAL):
        db_session.add(InvuSyncDay(branch_id=clayton_branch.id, business_date=DIA - timedelta(days=atras),
                                   matches=True, synced_at=ahora - timedelta(hours=1)))
    db_session.flush()
    viejo = db_session.query(InvuSyncDay).filter(InvuSyncDay.business_date == DIA - timedelta(days=4)).one()
    viejo.synced_at = ahora - timedelta(hours=30)
    no_cuadra = db_session.query(InvuSyncDay).filter(InvuSyncDay.business_date == DIA - timedelta(days=5)).one()
    no_cuadra.matches = False
    fallado = db_session.query(InvuSyncDay).filter(InvuSyncDay.business_date == DIA - timedelta(days=40)).one()
    fallado.error = "Invu respondió HTTP 500"
    db_session.commit()

    dias = invu_sales_sync.dias_pendientes(db_session, clayton_branch.id, DIA, ahora=ahora)
    assert dias == [DIA, DIA - timedelta(days=1), DIA - timedelta(days=4), DIA - timedelta(days=5), DIA - timedelta(days=40)]


def test_una_pasada_recorre_cada_sucursal_con_su_usuario(db_session, invu_ventas):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    resumen = invu_sales_sync.run_pass(db_session, hoy=DIA)

    assert set(resumen) == {"CLY", "OBR"}
    usuarios = {u for u, _, _ in invu_ventas["llamadas"]}
    assert usuarios == {"api_cly", "api_obr"}
    assert db_session.query(InvuSale).filter(InvuSale.branch_id == 1).count() > 0


def test_un_error_de_invu_queda_anotado_y_no_frena_la_pasada(db_session, invu_ventas, monkeypatch):
    def falla(cred, desde, hasta):
        raise invu_client.InvuError("Invu respondió HTTP 500 en 'citas/ordenesAllAdv'.")
    monkeypatch.setattr(invu_client, "list_orders", falla)

    resumen = invu_sales_sync.run_pass(db_session, hoy=DIA)
    assert resumen["CLY"]["errores"] > 0 and resumen["OBR"]["errores"] > 0
    assert db_session.query(InvuSyncDay).filter(InvuSyncDay.error.isnot(None)).count() > 0


# ==========================================================================
# API
# ==========================================================================
def test_link_no_es_para_agentes(client, clayton_agent, clayton_device, invu_ventas):
    res = client.get("/api/link/invu/status", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 403


def test_estado_por_sucursal(client, admin_user, invu_ventas, db_session, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 50.4}
    invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)

    res = client.get("/api/link/invu/status", headers=_headers(admin_user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    cly = next(b for b in body["branches"] if b["branch_code"] == "CLY")
    assert cly["configured"] is True and cly["days_synced"] == 1 and cly["last_day"] == "2026-09-23"


def test_sincronizar_a_mano_es_solo_del_admin(client, supervisor_user, clayton_device, invu_ventas):
    res = client.post("/api/link/invu/sync", json={}, headers=_headers(supervisor_user, clayton_device))
    assert res.status_code == 403


def test_sincronizar_a_mano_tiene_tope_de_dias(client, admin_user, invu_ventas):
    res = client.post("/api/link/invu/sync", json={"date_from": "2026-09-01", "date_to": "2026-09-20"},
                      headers=_headers(admin_user))
    assert res.status_code == 422


def test_sincronizar_a_mano_trae_los_dias_pedidos(client, admin_user, invu_ventas, clayton_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["totales"]["api_cly"] = {"total": 50.4}
    res = client.post("/api/link/invu/sync", json={"branch_id": clayton_branch.id, "date_from": "2026-09-23",
                                                   "date_to": "2026-09-23"}, headers=_headers(admin_user))
    assert res.status_code == 200, res.text
    dia = res.json()[0]["days"][0]
    assert dia["orders_count"] == 3 and dia["matches"] is True


def test_ventas_por_dia_y_por_plato(client, admin_user, invu_ventas, db_session, clayton_branch, obarrio_branch):
    invu_ventas["ordenes"]["api_cly"] = _dia_con_devolucion()
    invu_ventas["ordenes"]["api_obr"] = [_orden(500, [_linea(1, "MF364", "BOWL PERSONALIZADO NUEVO", 3, 56.85)])]
    invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)
    invu_sales_sync.sync_day(db_session, obarrio_branch, invu_client.Credenciales("api_obr", "clave"), DIA)

    res = client.get("/api/link/sales/daily?date_from=2026-09-23&date_to=2026-09-23", headers=_headers(admin_user))
    assert res.status_code == 200, res.text
    por_sucursal = {r["branch_code"]: r for r in res.json()}
    assert Decimal(por_sucursal["CLY"]["items_sold"]) == Decimal("4")
    assert Decimal(por_sucursal["CLY"]["net_total"]) == TOTAL_NETO

    res = client.get("/api/link/sales/items?date_from=2026-09-23&date_to=2026-09-23", headers=_headers(admin_user))
    top = res.json()[0]
    assert top["code"] == "MF364" and Decimal(top["quantity"]) == Decimal("5") and top["branches"] == 2


def test_el_supervisor_de_una_sucursal_solo_ve_la_suya(client, supervisor_user, clayton_device, invu_ventas,
                                                       db_session, obarrio_branch):
    invu_ventas["ordenes"]["api_obr"] = [_orden(500, [_linea(1, "MF364", "BOWL", 3, 56.85)])]
    invu_sales_sync.sync_day(db_session, obarrio_branch, invu_client.Credenciales("api_obr", "clave"), DIA)

    res = client.get(f"/api/link/sales/daily?date_from=2026-09-23&date_to=2026-09-23&branch_id={obarrio_branch.id}",
                     headers=_headers(supervisor_user, clayton_device))
    assert res.status_code == 200
    assert res.json() == []


def test_ventas_por_canal_restan_la_nota_de_credito(client, admin_user, invu_ventas, db_session, clayton_branch):
    ordenes = _dia_con_devolucion()
    ordenes[0]["desc_tipo_orden"] = "Pedidos Ya"
    invu_ventas["ordenes"]["api_cly"] = ordenes
    invu_sales_sync.sync_day(db_session, clayton_branch, invu_client.Credenciales("api_cly", "clave"), DIA)

    res = client.get("/api/link/sales/channels?date_from=2026-09-23&date_to=2026-09-23", headers=_headers(admin_user))
    assert res.status_code == 200, res.text
    canales = {r["order_type"]: r for r in res.json()}
    assert Decimal(canales["Pedidos Ya"]["net_total"]) == Decimal("47.90")
    # la 69028 (36.40) menos su nota (33.90); la nota no suma como orden
    assert Decimal(canales["Orden Normal"]["net_total"]) == Decimal("2.50")
    assert canales["Orden Normal"]["orders"] == 1
