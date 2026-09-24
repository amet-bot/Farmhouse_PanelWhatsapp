"""
Ventas de Invu POS → base local (Farmhouse Link, etapa 1).

Va en un solo sentido, como los proveedores: **Invu manda**. Acá se guarda una copia de cada
orden, con sus líneas y modificadores, para que Link pueda cruzar ventas con recetas,
cargamentos, merma y conteos sin salir a pedirle nada a Invu en cada consulta.

Tres reglas que salen de mirar la API de verdad (no solo la documentación):

- **Un usuario de API ve una sola sucursal.** No hay parámetro de sucursal en ninguna ruta, así
  que cada sucursal se sincroniza con sus propias credenciales (ver config.py).
- **Los días se cortan en hora de Panamá.** Invu filtra por la apertura de la orden en hora
  local; la ventana que se le pide es 00:00–23:59:59 de Panamá (UTC-5, sin horario de verano).
- **Una nota de crédito no borra la venta original: la marca.** Las líneas devueltas quedan
  "Devuelto NC" en la orden original y la nota aparece como una orden aparte. Por eso una línea
  cuenta como vendida si está "Agregado" dentro de una orden que no es nota de crédito, y por
  eso los últimos días se vuelven a pedir: la devolución de hoy cambia una orden de anteayer.

Qué se pide y cuándo (ver `dias_pendientes`): hoy y ayer en cada pasada; los cinco días
anteriores una vez por día; y el historial hacia atrás de a poco, hasta `DIAS_DE_HISTORIAL`.
Cada día son dos llamadas (órdenes + totales de control), muy por debajo de la cuota de Invu.
"""
import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from config import settings
from database import SessionLocal
from models.branch import Branch
from models.invu_sales import InvuMenuItem, InvuSale, InvuSaleLine, InvuSaleModifier, InvuSyncDay
from services import invu_client

logger = logging.getLogger("farmhouse.invu.ventas")

# Panamá no tiene horario de verano: un desfase fijo alcanza y no depende de tener tzdata
# instalado (en Windows no viene).
PANAMA_TZ = timezone(timedelta(hours=-5))

# Cada cuánto corre una pasada. Link no es un tablero en vivo: con ver lo de hace un par de
# horas alcanza para decidir qué pedir.
SYNC_INTERVAL_SECONDS = 3 * 60 * 60
STARTUP_DELAY_SECONDS = 60

# Hasta dónde se trae el historial, y cuántos días viejos por pasada: la primera carga se reparte
# en varias pasadas para no gastar de un golpe la cuota diaria, que Invu dice que puede cambiar.
DIAS_DE_HISTORIAL = 90
DIAS_VIEJOS_POR_PASADA = 15

# Los días recientes se vuelven a pedir una vez por día, para agarrar devoluciones tardías.
DIAS_A_REVISAR = 7
REVISAR_CADA = timedelta(hours=20)

# El menú cambia poco; una vez por día.
MENU_CADA = timedelta(hours=24)

# Freno entre días: son dos llamadas por día y la API admite 60 por minuto.
PAUSA_ENTRE_DIAS = 2.5

# Diferencia tolerada entre el total nuestro y el de Invu (redondeos de centavos).
TOLERANCIA = Decimal("0.05")


# ==========================================================================
# Conversión de lo que manda Invu
# ==========================================================================
def _entero(valor: Any) -> Optional[int]:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _decimal(valor: Any, exponente: str) -> Optional[Decimal]:
    """Invu manda números como número o como texto, y con basura de coma flotante (62.849999…)."""
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal(exponente))
    except (InvalidOperation, ValueError):
        return None


def _dinero(valor: Any) -> Optional[Decimal]:
    return _decimal(valor, "0.01")


def _cantidad(valor: Any) -> Decimal:
    return _decimal(valor, "0.001") or Decimal("0")


def _texto(valor: Any, tope: int) -> Optional[str]:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto in ("0000-00-00", "0000-00-00 00:00:00", "null"):
        return None
    return texto[:tope]


def _local_a_utc(valor: Any) -> Optional[datetime]:
    """'2026-09-23 08:10:51' en hora de Panamá → datetime UTC sin zona, como guarda el proyecto."""
    texto = _texto(valor, 30)
    if not texto:
        return None
    try:
        local = datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PANAMA_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_sin_zona(momento: datetime) -> datetime:
    """MySQL devuelve fechas sin zona; para comparar, todo en UTC sin zona."""
    if momento.tzinfo is not None:
        return momento.astimezone(timezone.utc).replace(tzinfo=None)
    return momento


def hoy_panama() -> date:
    return datetime.now(PANAMA_TZ).date()


def ventana_del_dia(dia: date) -> Tuple[int, int]:
    """Epoch de 00:00:00 y 23:59:59 de ese día en Panamá: la ventana que se le pide a Invu."""
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=PANAMA_TZ)
    return int(inicio.timestamp()), int(inicio.timestamp()) + 86399


def _es_nota_de_credito(orden: Dict[str, Any]) -> bool:
    pagada = str(orden.get("pagada") or "").strip().lower()
    return pagada.startswith("nota") or pagada.startswith("nc") or str(orden.get("num_cita") or "").startswith("NC-")


def _linea_cuenta(estado: Optional[str], orden_es_nota: bool) -> bool:
    """
    Si la línea es plato vendido. Se descartan las que se sabe que no lo son en vez de aceptar
    solo "Agregado": un estado nuevo que Invu invente mañana no debería hacer desaparecer ventas.
    """
    if orden_es_nota:
        return False
    estado = (estado or "").lower()
    return not any(marca in estado for marca in ("devuelto", "elimin", "anulad"))


# ==========================================================================
# Sucursales con credenciales
# ==========================================================================
def sucursales_configuradas(db) -> List[Tuple[Branch, invu_client.Credenciales]]:
    """Las sucursales que tienen usuario de API y existen en la base, en el orden de config."""
    credenciales = settings.invu_branch_credentials()
    if not credenciales:
        return []
    por_codigo = {
        b.code: b for b in db.query(Branch).filter(Branch.code.in_(list(credenciales))).all()
    }
    return [
        (por_codigo[code], invu_client.Credenciales(*credenciales[code]))
        for code in credenciales
        if code in por_codigo
    ]


def hay_credenciales() -> bool:
    return bool(settings.invu_branch_credentials())


# ==========================================================================
# Un día de una sucursal
# ==========================================================================
def _copiar_orden(venta: InvuSale, orden: Dict[str, Any], dia: date, es_nota: bool, ahora: datetime) -> None:
    totales = orden.get("totales") or {}
    venta.business_date = dia
    venta.opened_at = _local_a_utc(orden.get("fecha_apertura_date"))
    venta.closed_at = _local_a_utc(orden.get("fecha_cierre_date"))
    venta.status = _texto(orden.get("pagada"), 30) or "Desconocido"
    venta.is_credit_note = es_nota
    venta.order_type = _texto(orden.get("desc_tipo_orden"), 60)
    venta.channel = _texto(orden.get("tipo_integracion_desc"), 60)
    venta.subtotal = _dinero(totales.get("subtotal", orden.get("subtotal")))
    venta.discount = _dinero(totales.get("descuento"))
    venta.tax = _dinero(totales.get("tax", orden.get("tax")))
    venta.total = _dinero(totales.get("total", orden.get("total")))
    venta.synced_at = ahora


def _armar_lineas(venta: InvuSale, orden: Dict[str, Any], branch_id: int, dia: date, es_nota: bool) -> int:
    lineas = 0
    for item in orden.get("items") or []:
        linea_id = _entero(item.get("id"))
        if linea_id is None:
            continue
        totales = item.get("totales_item") or {}
        estado = _texto(item.get("desc_status_venta_item"), 30)
        linea = InvuSaleLine(
            branch_id=branch_id,
            business_date=dia,
            invu_line_id=linea_id,
            invu_item_id=_entero(item.get("id_item")),
            code=_texto(item.get("codigo"), 50),
            name=_texto(item.get("desc_item"), 200) or "(sin nombre)",
            category=_texto(item.get("categoria"), 100),
            quantity=_cantidad(item.get("cantidad")),
            unit_price=_dinero(item.get("precioSugerido")),
            discount=_dinero(totales.get("descuento_total")),
            total=_dinero(totales.get("total")),
            status=estado,
            counted=_linea_cuenta(estado, es_nota),
        )
        for modif in item.get("modif") or []:
            # Invu manda la clave de la cantidad con un acento grave colado ("cantidad_ve`ndida")
            # en algunas rutas; se aceptan las dos.
            cantidad = modif.get("cantidad_vendida", modif.get("cantidad_ve`ndida"))
            linea.modifiers.append(InvuSaleModifier(
                invu_modifier_id=_entero(modif.get("id")),
                code=_texto(modif.get("codigo"), 50),
                name=_texto(modif.get("nombre"), 200) or "(sin nombre)",
                quantity=_cantidad(cantidad),
                total=_dinero(modif.get("total")),
            ))
        venta.lines.append(linea)
        lineas += 1
    return lineas


def sync_day(db, branch: Branch, credenciales: invu_client.Credenciales, dia: date) -> InvuSyncDay:
    """
    Deja ese día de esa sucursal igual a como está en Invu: crea las órdenes nuevas, rehace las
    que cambiaron y borra las que ya no vienen (una orden eliminada en Invu después de
    sincronizada). Después pide el total del día a Invu y anota si cuadra.
    """
    desde, hasta = ventana_del_dia(dia)
    ordenes = invu_client.list_orders(credenciales, desde, hasta)
    ahora = datetime.now(timezone.utc)

    existentes = {
        v.invu_order_id: v
        for v in db.query(InvuSale).filter(InvuSale.branch_id == branch.id, InvuSale.business_date == dia).all()
    }
    vistas = set()
    lineas = 0
    neto = Decimal("0")

    for orden in ordenes:
        orden_id = _entero(orden.get("id"))
        if orden_id is None or orden_id in vistas:
            continue
        vistas.add(orden_id)
        es_nota = _es_nota_de_credito(orden)

        venta = existentes.get(orden_id)
        if venta is None:
            # Pudo quedar guardada con otra fecha si en Invu le cambiaron la apertura.
            venta = db.query(InvuSale).filter(
                InvuSale.branch_id == branch.id, InvuSale.invu_order_id == orden_id
            ).first()
        if venta is None:
            venta = InvuSale(branch_id=branch.id, invu_order_id=orden_id)
            db.add(venta)
        else:
            # Las líneas se rehacen enteras. Se borran y se escriben antes de agregar las nuevas:
            # SQLAlchemy inserta antes de borrar, y la misma línea chocaría con su versión vieja.
            for vieja in list(venta.lines):
                db.delete(vieja)
            db.flush()
            db.expire(venta, ["lines"])

        _copiar_orden(venta, orden, dia, es_nota, ahora)
        lineas += _armar_lineas(venta, orden, branch.id, dia, es_nota)
        if venta.total is not None:
            neto += -venta.total if es_nota else venta.total

    for orden_id, venta in existentes.items():
        if orden_id not in vistas:
            db.delete(venta)

    totales_invu = invu_client.order_totals(credenciales, desde, hasta)
    total_invu = _dinero(totales_invu.get("total"))

    registro = db.query(InvuSyncDay).filter(
        InvuSyncDay.branch_id == branch.id, InvuSyncDay.business_date == dia
    ).first()
    if registro is None:
        registro = InvuSyncDay(branch_id=branch.id, business_date=dia)
        db.add(registro)
    registro.orders_count = len(vistas)
    registro.lines_count = lineas
    registro.net_total = neto
    registro.invu_total = total_invu
    registro.matches = (abs(neto - total_invu) <= TOLERANCIA) if total_invu is not None else None
    registro.error = None
    registro.synced_at = ahora

    db.commit()
    if registro.matches is False:
        logger.warning(
            f"[Invu] {branch.code} {dia}: el total no cuadra (nuestro {neto} vs Invu {total_invu})."
        )
    return registro


def _anotar_error(db, branch: Branch, dia: date, mensaje: str) -> None:
    db.rollback()
    registro = db.query(InvuSyncDay).filter(
        InvuSyncDay.branch_id == branch.id, InvuSyncDay.business_date == dia
    ).first()
    if registro is None:
        registro = InvuSyncDay(branch_id=branch.id, business_date=dia)
        db.add(registro)
    registro.error = mensaje[:2000]
    registro.synced_at = datetime.now(timezone.utc)
    db.commit()


# ==========================================================================
# Menú de una sucursal
# ==========================================================================
def sync_menu(db, branch: Branch, credenciales: invu_client.Credenciales) -> Dict[str, int]:
    """Copia los platos activos. Los que dejaron de venir quedan inactivos; nunca se borran."""
    ahora = datetime.now(timezone.utc)
    existentes = {m.invu_id: m for m in db.query(InvuMenuItem).filter(InvuMenuItem.branch_id == branch.id).all()}
    vistos = set()
    creados = 0

    for fila in invu_client.iter_menu_items(credenciales):
        invu_id = _entero(fila.get("id"))
        if invu_id is None or invu_id in vistos:
            continue
        vistos.add(invu_id)
        plato = existentes.get(invu_id)
        if plato is None:
            plato = InvuMenuItem(branch_id=branch.id, invu_id=invu_id)
            db.add(plato)
            creados += 1
        plato.code = _texto(fila.get("code"), 50)
        plato.name = _texto(fila.get("name"), 200) or "(sin nombre)"
        plato.suggested_price = _dinero(fila.get("suggested_price"))
        plato.active = True
        plato.synced_at = ahora

    desactivados = 0
    for invu_id, plato in existentes.items():
        if invu_id not in vistos and plato.active:
            plato.active = False
            desactivados += 1

    db.commit()
    return {"recibidos": len(vistos), "creados": creados, "desactivados": desactivados}


def _menu_al_dia(db, branch_id: int, ahora: datetime) -> bool:
    ultimo = db.query(func.max(InvuMenuItem.synced_at)).filter(InvuMenuItem.branch_id == branch_id).scalar()
    return ultimo is not None and _utc_sin_zona(ahora) - _utc_sin_zona(ultimo) < MENU_CADA


# ==========================================================================
# Qué días pedir
# ==========================================================================
def dias_pendientes(db, branch_id: int, hoy: date, ahora: Optional[datetime] = None) -> List[date]:
    """
    Los días de esa sucursal que hay que pedir en esta pasada, del más nuevo al más viejo:

    1. Hoy y ayer, siempre: hoy sigue vendiendo y ayer puede haber cerrado órdenes tarde.
    2. De anteayer a `DIAS_A_REVISAR` atrás, si nunca se trajo, falló, no cuadró, o hace más de
       `REVISAR_CADA` que no se revisa (devoluciones tardías).
    3. El historial hasta `DIAS_DE_HISTORIAL`: los que nunca se trajeron o fallaron, de a
       `DIAS_VIEJOS_POR_PASADA`.
    """
    ahora = _utc_sin_zona(ahora or datetime.now(timezone.utc))
    limite = hoy - timedelta(days=DIAS_DE_HISTORIAL - 1)
    registros = {
        r.business_date: r
        for r in db.query(InvuSyncDay).filter(
            InvuSyncDay.branch_id == branch_id, InvuSyncDay.business_date >= limite
        ).all()
    }

    pendientes = [hoy, hoy - timedelta(days=1)]

    for atras in range(2, DIAS_A_REVISAR + 1):
        dia = hoy - timedelta(days=atras)
        r = registros.get(dia)
        if (
            r is None
            or r.error
            or r.matches is False
            or ahora - _utc_sin_zona(r.synced_at) >= REVISAR_CADA
        ):
            pendientes.append(dia)

    viejos = []
    dia = hoy - timedelta(days=DIAS_A_REVISAR + 1)
    while dia >= limite and len(viejos) < DIAS_VIEJOS_POR_PASADA:
        r = registros.get(dia)
        if r is None or r.error:
            viejos.append(dia)
        dia -= timedelta(days=1)

    return pendientes + viejos


# ==========================================================================
# Pasada completa y loop
# ==========================================================================
def run_pass(db, hoy: Optional[date] = None) -> Dict[str, Any]:
    """Una pasada por todas las sucursales configuradas. Un día que falla no frena a los demás."""
    hoy = hoy or hoy_panama()
    resumen: Dict[str, Any] = {}

    for branch, credenciales in sucursales_configuradas(db):
        detalle = {"dias": 0, "errores": 0, "menu": None}
        try:
            if not _menu_al_dia(db, branch.id, datetime.now(timezone.utc)):
                detalle["menu"] = sync_menu(db, branch, credenciales)
        except Exception as e:
            db.rollback()
            logger.warning(f"[Invu] {branch.code}: no se pudo traer el menú: {e}")

        try:
            pendientes = dias_pendientes(db, branch.id, hoy)
        except Exception:
            db.rollback()
            logger.exception(f"[Invu] {branch.code}: no se pudo calcular qué días faltan.")
            continue

        for dia in pendientes:
            try:
                sync_day(db, branch, credenciales, dia)
                detalle["dias"] += 1
            except invu_client.InvuError as e:
                detalle["errores"] += 1
                _anotar_error(db, branch, dia, str(e))
                logger.warning(f"[Invu] {branch.code} {dia}: {e}")
                if "cuota" in str(e).lower() or "credenciales" in str(e).lower():
                    break  # sin cuota o sin acceso, seguir pidiendo días solo gasta tiempo
            except Exception as e:
                detalle["errores"] += 1
                _anotar_error(db, branch, dia, f"Error inesperado: {e}")
                logger.exception(f"[Invu] {branch.code} {dia}: error inesperado.")
            time.sleep(PAUSA_ENTRE_DIAS)

        resumen[branch.code] = detalle

    logger.info(f"[Invu] Pasada de ventas terminada: {resumen}")
    return resumen


def _pasada_en_segundo_plano() -> None:
    db = SessionLocal()
    try:
        run_pass(db)
    except Exception:
        logger.exception("[Invu] Falló una pasada de la sincronización de ventas.")
    finally:
        db.close()


async def run_sales_sync_loop() -> None:
    """
    Tercer loop en segundo plano del proyecto, con las mismas reglas que los otros dos (ver
    services/invu_sync.py): nunca bajo pytest, una pasada que falla no tumba el loop, y la parte
    bloqueante va a un hilo aparte.
    """
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_pasada_en_segundo_plano)
        except Exception:
            logger.exception("[Invu] Error en el loop de ventas.")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def debe_arrancar_loop() -> bool:
    return "PYTEST_CURRENT_TEST" not in os.environ and hay_credenciales()
