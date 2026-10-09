"""
Inventario · Cálculos compartidos: existencias, costos, recetas y ventas de Invu, alcance por sucursal, avisos.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models.inventory_item import InventoryItem
from models.invu_sales import InvuSale, InvuSaleLine, InvuSaleModifier
from models.native_push import NativePushToken
from models.push_subscription import PushSubscription
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem
from models.stock_count import StockCount, StockCountItem
from models.transfer import Transfer, TransferItem
from models.consumption import ConsumptionRecord, ConsumptionItem
from services import fcm_service, invu_sales_sync, push_service
from security.permissions import has_permission

from .common import logger



_ESTADO_TEXTO = {
    "falto": "faltó", "sobro": "sobró", "equivocado": "llegó equivocado", "danado": "llegó dañado",
}



def _cant(valor: Decimal, unidad: Optional[str]) -> str:
    texto = f"{Decimal(valor).normalize():f}"
    return f"{texto} {unidad or ''}".strip()



def _describir_problema(line: ShipmentItem) -> str:
    """"Tomate: facturado 10 kg, llegó 8 kg (faltó 2 kg) — nota"."""
    item = line.inventory_item
    partes = []
    if line.invoiced_quantity is not None:
        partes.append(f"facturado {_cant(line.invoiced_quantity, item.unit)}, llegó {_cant(line.quantity, item.unit)}")
    estado = _ESTADO_TEXTO.get(line.line_status, line.line_status or "")
    if line.line_status in ("falto", "sobro") and line.invoiced_quantity is not None:
        diferencia = abs(Decimal(line.invoiced_quantity) - Decimal(line.quantity))
        estado = f"{estado} {_cant(diferencia, item.unit)}"
    texto = f"{item.name}: " + (f"{', '.join(partes)} ({estado})" if partes else estado)
    if line.line_note:
        texto += f" — {line.line_note}"
    return texto



def _avisar_diferencias_background(branch_id: int, title: str, body: str, url: str, tag: str) -> None:
    """El aviso sale después de responder: mandar push no puede demorar el registro."""
    db = SessionLocal()
    try:
        push_service.notify_branch_staff(db, branch_id, title, body, url, tag=tag, managers_only=True)
    except Exception as e:  # un aviso fallido no deshace el cargamento
        logger.error(f"[Push cargamento] sucursal {branch_id}: {e}", exc_info=True)
    finally:
        db.close()



def _hay_a_quien_avisar(db: Session, branch_id: int) -> bool:
    """
    Si el aviso le puede llegar a algún encargado: por el navegador (Web Push) o por la app de
    Android (Firebase), según lo que esté configurado y lo que tenga activado cada uno.
    """
    encargado = and_(
        User.active == True,  # noqa: E712
        or_(User.role == "admin",
            and_(User.role == "supervisor", or_(User.branch_id.is_(None), User.branch_id == branch_id))),
    )
    if push_service.is_push_configured() and db.query(PushSubscription.id).join(
        User, User.id == PushSubscription.user_id
    ).filter(encargado).first() is not None:
        return True
    return fcm_service.is_configured() and db.query(NativePushToken.id).join(
        User, User.id == NativePushToken.user_id
    ).filter(encargado).first() is not None



# ==========================================================================
# Merma
# ==========================================================================
def _visible_branch_filter(current_user: User, branch_id: Optional[int]):
    """
    A qué sucursal mira esta consulta. Calcado de list_shipments: admin y supervisor global
    eligen (o ven todas), el resto queda encerrado en la suya sin importar lo que pida.
    Devuelve el branch_id efectivo, o None cuando significa "todas".
    """
    if current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None):
        return branch_id
    if current_user.branch_id is None:
        # Un agente sin sucursal (dato inválido) devolvía None = "todas": veía el inventario de
        # todas las sucursales. Falla cerrado.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu usuario no tiene una sucursal asignada.")
    return current_user.branch_id



def _last_costs_map(db: Session, branch_id: int) -> dict:
    """
    Último costo conocido de cada insumo en esa sucursal, en una sola pasada y no una consulta
    por insumo: un conteo de arranque trae cientos de renglones.
    """
    filas = (
        db.query(ShipmentItem.inventory_item_id, ShipmentItem.unit_cost)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(Shipment.branch_id == branch_id, ShipmentItem.unit_cost.isnot(None))
        .order_by(Shipment.received_at.asc(), ShipmentItem.id.asc())
        .all()
    )
    # Ordenado de viejo a nuevo: el último que se escribe es el más reciente.
    return {item_id: costo for item_id, costo in filas}



# Un traslado sale de la sucursal de origen al despacharse y entra a la de destino al recibirse
# (mismo criterio que los movimientos transfer_out/transfer_in del libro, routers/transfers.py).
_TRANSFER_OUT_STATUSES = ("dispatched", "received")



def _transfer_net_map(db: Session, branch_id: Optional[int], item_ids: Optional[List[int]] = None) -> dict:
    """
    Neto de traslados por insumo (lo que entró por traslado menos lo que salió), en esa sucursal
    o en todas si branch_id es None. Sin esto la existencia ignoraba los traslados: quien mandaba
    seguía mostrando lo mandado, quien recibía no lo veía, y el siguiente conteo "descubría" la
    diferencia como un ajuste — que en el libro de movimientos quedaba contado dos veces.

    Sumando todas las sucursales, lo despachado y todavía no recibido resta: está en camino.
    """
    salidas_q = (
        db.query(TransferItem.inventory_item_id, func.coalesce(func.sum(TransferItem.quantity), 0))
        .join(Transfer, Transfer.id == TransferItem.transfer_id)
        .filter(Transfer.status.in_(_TRANSFER_OUT_STATUSES))
    )
    entradas_q = (
        db.query(TransferItem.inventory_item_id, func.coalesce(func.sum(TransferItem.quantity), 0))
        .join(Transfer, Transfer.id == TransferItem.transfer_id)
        .filter(Transfer.status == "received")
    )
    if branch_id is not None:
        salidas_q = salidas_q.filter(Transfer.from_branch_id == branch_id)
        entradas_q = entradas_q.filter(Transfer.to_branch_id == branch_id)
    if item_ids is not None:
        salidas_q = salidas_q.filter(TransferItem.inventory_item_id.in_(item_ids))
        entradas_q = entradas_q.filter(TransferItem.inventory_item_id.in_(item_ids))

    neto: dict = {}
    for item_id, cantidad in entradas_q.group_by(TransferItem.inventory_item_id).all():
        neto[item_id] = neto.get(item_id, Decimal("0")) + Decimal(cantidad)
    for item_id, cantidad in salidas_q.group_by(TransferItem.inventory_item_id).all():
        neto[item_id] = neto.get(item_id, Decimal("0")) - Decimal(cantidad)
    return neto



def _consumo_map(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """Lo que el equipo registró como consumido (routers/consumption.py), por insumo, en esa sucursal."""
    if not item_ids:
        return {}
    return dict(
        db.query(ConsumptionItem.inventory_item_id, func.coalesce(func.sum(ConsumptionItem.quantity), 0))
        .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
        .filter(ConsumptionRecord.branch_id == branch_id, ConsumptionItem.inventory_item_id.in_(item_ids))
        .group_by(ConsumptionItem.inventory_item_id)
        .all()
    )



def _on_hand_map(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """Existencia actual (entradas - mermas - consumo registrado + diferencias de conteo ± traslados) de esos insumos en esa sucursal."""
    if not item_ids:
        return {}

    entradas = dict(
        db.query(ShipmentItem.inventory_item_id, func.coalesce(func.sum(ShipmentItem.quantity), 0))
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(Shipment.branch_id == branch_id, ShipmentItem.inventory_item_id.in_(item_ids))
        .group_by(ShipmentItem.inventory_item_id)
        .all()
    )
    salidas = dict(
        db.query(WasteItem.inventory_item_id, func.coalesce(func.sum(WasteItem.quantity), 0))
        .join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id)
        .filter(WasteRecord.branch_id == branch_id, WasteItem.inventory_item_id.in_(item_ids))
        .group_by(WasteItem.inventory_item_id)
        .all()
    )
    ajustes = dict(
        db.query(StockCountItem.inventory_item_id, func.coalesce(func.sum(StockCountItem.difference), 0))
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(StockCount.branch_id == branch_id, StockCountItem.inventory_item_id.in_(item_ids))
        .group_by(StockCountItem.inventory_item_id)
        .all()
    )
    traslados = _transfer_net_map(db, branch_id, item_ids)
    consumo = _consumo_map(db, branch_id, item_ids)
    return {
        item_id: (
            Decimal(entradas.get(item_id, 0)) - Decimal(salidas.get(item_id, 0)) - Decimal(consumo.get(item_id, 0))
            + Decimal(ajustes.get(item_id, 0)) + traslados.get(item_id, Decimal("0"))
        )
        for item_id in item_ids
    }



def _vendido_desde_conteo(db: Session, branch_id: int, item_ids: Optional[List[int]] = None) -> dict:
    """
    Por insumo: lo que se usó en los platos vendidos (ventas de Invu × recetas) desde su ÚLTIMO
    conteo en esa sucursal.

    Desde el último conteo y no desde siempre: el conteo ya fijó cuánto había, y la diferencia
    que guardó absorbió todo lo que se cocinó antes. Restar ventas anteriores las contaría dos
    veces. Por lo mismo, un insumo que nunca se contó no descuenta nada todavía: sin punto de
    partida no se sabe cuánto había cuando empezaron las ventas. Y sin receta en Invu no hay de
    dónde sacar cuánto se usó.
    """
    con_receta = _insumos_con_receta(db, branch_id)
    if item_ids is not None:
        con_receta &= set(item_ids)
    if not con_receta:
        return {}
    ultimos = (
        db.query(StockCountItem.inventory_item_id, func.max(StockCount.counted_at))
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(StockCount.branch_id == branch_id, StockCountItem.inventory_item_id.in_(con_receta))
        .group_by(StockCountItem.inventory_item_id)
        .all()
    )
    if not ultimos:
        return {}
    # Los insumos de un mismo conteo comparten el momento: una consulta de ventas por conteo.
    grupos: dict = {}
    for item_id, contado in ultimos:
        grupos.setdefault(contado, []).append(item_id)
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    recetas_insumos = _recetas_de_sucursal(db, branch_id)
    vendido: dict = {}
    for contado, ids in grupos.items():
        # Si el equipo registró consumo a mano de un insumo desde ese conteo, ese registro manda
        # y no se le estima además el uso por ventas (se descontaría dos veces).
        con_manual = {
            r[0] for r in db.query(ConsumptionItem.inventory_item_id)
            .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
            .filter(ConsumptionRecord.branch_id == branch_id, ConsumptionItem.inventory_item_id.in_(ids), ConsumptionRecord.occurred_at > contado)
            .distinct().all()
        }
        ids = [i for i in ids if i not in con_manual]
        if not ids:
            continue
        uso = _uso_por_ventas(db, branch_id, contado, ahora, recetas_insumos=recetas_insumos)
        for item_id in ids:
            if uso.get(item_id):
                vendido[item_id] = uso[item_id]
    return vendido



def _existencia_map(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """
    Lo que hay de verdad: lo que dicen los registros (`_on_hand_map`) menos lo que se vendió
    desde el último conteo. Es lo que se MUESTRA (existencias, días que alcanza, aviso de merma).
    El conteo, en cambio, guarda su diferencia contra los registros solos: así el conteo
    siguiente arranca limpio y su análisis descuenta las ventas una sola vez.
    """
    registros = _on_hand_map(db, branch_id, item_ids)
    vendido = _vendido_desde_conteo(db, branch_id, item_ids)
    return {iid: cantidad - vendido.get(iid, Decimal("0")) for iid, cantidad in registros.items()}



def _familia_de_unidad(unit: Optional[str]) -> tuple:
    """
    ("peso", gramos por unidad) | ("volumen", ml por unidad) | ("unidad", 1).
    Lo que no es peso ni volumen ("unidad", "caja", "bolsa"...) se cuenta por pieza.
    """
    fam = _UNIT_FAMILY.get((unit or "").strip().lower())
    if fam and fam[0] in ("peso", "volumen"):
        return fam[0], fam[1] * 1000
    return "unidad", Decimal("1")



def _dias_utc(desde: date, hasta: date) -> tuple:
    """[desde 00:00, hasta+1 00:00) de Panamá, en UTC sin zona (como se guarda todo)."""
    tz = invu_sales_sync.PANAMA_TZ
    ini = datetime.combine(desde, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return ini, fin



# ---- Análisis de merma ----
# Cuántos kg es una unidad de cada nombre de unidad que usan los insumos (los de Invu vienen como
# "gramos" / "kilogramo"; los cargados a mano, como "kg"). Lo que no está acá no es un peso.
_KG_POR_UNIDAD = {
    "kg": Decimal("1"), "kilo": Decimal("1"), "kilos": Decimal("1"), "kilogramo": Decimal("1"), "kilogramos": Decimal("1"),
    "g": Decimal("0.001"), "gr": Decimal("0.001"), "gramo": Decimal("0.001"), "gramos": Decimal("0.001"),
    "lb": Decimal("0.45359237"), "libra": Decimal("0.45359237"), "libras": Decimal("0.45359237"),
    "oz": Decimal("0.028349523"), "onza": Decimal("0.028349523"), "onzas": Decimal("0.028349523"),
}



# ---- Merma × recetas de Invu ----
# Para pasar la cantidad de una receta a la unidad del insumo: (familia, factor a la base de la
# familia). Solo se convierte dentro de la misma familia (peso con peso, volumen con volumen).
_UNIT_FAMILY = {
    **{k: ("peso", v) for k, v in _KG_POR_UNIDAD.items()},
    "ml": ("volumen", Decimal("0.001")), "mililitro": ("volumen", Decimal("0.001")), "mililitros": ("volumen", Decimal("0.001")),
    "l": ("volumen", Decimal("1")), "litro": ("volumen", Decimal("1")), "litros": ("volumen", Decimal("1")),
    "unidad": ("unidad", Decimal("1")), "unidades": ("unidad", Decimal("1")), "u": ("unidad", Decimal("1")), "und": ("unidad", Decimal("1")),
}



def _a_unidad_del_insumo(
    cantidad: Decimal, unidad_receta: Optional[str], unidad_insumo: Optional[str], piece_size: Optional[Decimal] = None,
    grams_per_ml: Optional[Decimal] = None,
) -> Optional[Decimal]:
    """
    La cantidad de la receta en la unidad del insumo, o None si no se puede convertir.

    Entre familias distintas (la receta en gramos y el insumo por unidad, o al revés) se usa lo
    que pesa una pieza del insumo (`piece_size`: gramos, o ml si el insumo es líquido), el mismo
    dato que aprende la merma. Entre peso y volumen (receta en g, insumo en ml) se usa
    `grams_per_ml`, que pone una persona en Recetas → Unidades. Sin esos datos no se inventa:
    queda None y quien llama lo informa.
    """
    r = (unidad_receta or "").strip().lower()
    i = (unidad_insumo or "").strip().lower()
    if not r or r == i:
        return cantidad
    fr, fi = _UNIT_FAMILY.get(r), _UNIT_FAMILY.get(i)
    if fr and fi and fr[0] == fi[0]:
        return cantidad * fr[1] / fi[1]
    if fr and fi and {fr[0], fi[0]} == {"peso", "volumen"}:
        if grams_per_ml is None or Decimal(grams_per_ml) <= 0:
            return None
        base = cantidad * fr[1] * 1000                    # gramos o ml de la receta
        otra = base / Decimal(grams_per_ml) if fr[0] == "peso" else base * Decimal(grams_per_ml)
        return otra / (fi[1] * 1000)                      # en la unidad del insumo (g, kg, ml, l...)
    if not fr or piece_size is None or Decimal(piece_size) <= 0:
        return None
    pieza = Decimal(piece_size)
    fam_i, base_i = _familia_de_unidad(unidad_insumo)   # base_i: g (o ml) por unidad del insumo
    if fam_i == "unidad" and fr[0] in ("peso", "volumen"):
        return cantidad * fr[1] * 1000 / pieza          # gramos (o ml) de la receta / lo que trae una pieza
    if fr[0] == "unidad" and fam_i in ("peso", "volumen"):
        return cantidad * pieza / base_i                # piezas x gramos por pieza, en la unidad del insumo
    return None



# Quien registró una merma puede borrarla solo, sin pedírselo a nadie, mientras sea un error
# reciente (se equivocó de insumo, de cantidad o la cargó dos veces). Pasado ese plazo la merma ya
# entró en los reportes y borrarla es corregir historia: eso queda para supervisor o admin.
WASTE_SELF_DELETE_WINDOW = timedelta(hours=24)



def _chequear_quien_borra(current_user: User, dueno_id: int, creado: datetime, que: str) -> None:
    """Supervisor/admin borran cualquiera; el resto, solo lo suyo y dentro de las 24 horas."""
    if has_permission(current_user, "inventory.adjust"):
        return
    if dueno_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Solo quien registró {que}, o un supervisor, puede borrarlo.",
        )
    cargado = creado if creado.tzinfo else creado.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - cargado > WASTE_SELF_DELETE_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pasaron más de 24 horas desde que se cargó: pedile a un supervisor que lo borre.",
        )



def _chequear_sin_conteo_posterior(db: Session, branch_id: int, item_ids: List[int], creado: datetime, que: str) -> None:
    """
    Un conteo posterior de esos insumos ya dejó la existencia en lo contado, y su "lo que decía
    el sistema" incluía este registro. Borrarlo ahora dejaría la existencia corrida (por debajo de
    lo real si era un cargamento, por encima si era una merma). En ese caso no se borra: se
    corrige con un conteo nuevo, que es lo que refleja lo que de verdad hay.
    """
    creado_utc = creado.astimezone(timezone.utc).replace(tzinfo=None) if creado.tzinfo else creado
    contados = db.query(InventoryItem.name, func.max(StockCount.counted_at)).select_from(StockCountItem).join(
        StockCount, StockCount.id == StockCountItem.stock_count_id
    ).join(InventoryItem, InventoryItem.id == StockCountItem.inventory_item_id).filter(
        StockCount.branch_id == branch_id,
        StockCount.counted_at > creado_utc,
        StockCountItem.inventory_item_id.in_(item_ids or [0]),
    ).group_by(InventoryItem.name).all()
    if contados:
        nombres = ", ".join(sorted(n for n, _ in contados))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"No se puede borrar {que}: después se contó {nombres} y ese conteo ya dejó la "
                    "existencia en lo que había. Si estaba mal, se corrige con un conteo nuevo."),
        )



def _recetas_de_sucursal(db: Session, branch_id: int) -> tuple:
    """Las recetas efectivas de la sucursal por (tipo, id de Invu) y el catálogo por id de Invu.
    Las de Invu de esa sucursal mandan; donde no hay, se usa la del mismo plato en otra sucursal
    o, si es un producto de reventa, 1 unidad del insumo del mismo nombre (services/recipe_resolver)."""
    from services.recipe_resolver import resolver
    recetas, insumos, _origen = resolver(db, branch_id)
    return recetas, insumos



def _uso_por_ventas(
    db: Session, branch_id: int, desde: datetime, hasta: datetime,
    sin_conversion: Optional[set] = None, recetas_insumos: Optional[tuple] = None,
) -> dict:
    """
    Lo que se usó de cada insumo en los platos vendidos entre dos momentos (UTC), según las
    recetas de Invu: Σ platos × receta + Σ modificadores × receta (mismo criterio que
    /waste/recipe-usage). El momento de cada venta es la apertura de la orden en la caja.

    Si se pasa `sin_conversion`, ahí se anotan los insumos con alguna receta vendida cuya unidad
    no se pudo pasar a la del insumo: su uso quedó corto y no hay que leerlo como faltante.
    """
    momento = func.coalesce(InvuSale.opened_at, InvuSale.closed_at)
    filtros = (
        InvuSaleLine.branch_id == branch_id,
        InvuSaleLine.counted == True,  # noqa: E712
        momento >= desde,
        momento < hasta,
    )
    platos = db.query(InvuSaleLine.invu_item_id, func.sum(InvuSaleLine.quantity)).join(
        InvuSale, InvuSale.id == InvuSaleLine.sale_id
    ).filter(*filtros, InvuSaleLine.invu_item_id.isnot(None)).group_by(InvuSaleLine.invu_item_id).all()
    mods = db.query(InvuSaleModifier.invu_modifier_id, func.sum(InvuSaleModifier.quantity)).join(
        InvuSaleLine, InvuSaleLine.id == InvuSaleModifier.line_id
    ).join(InvuSale, InvuSale.id == InvuSaleLine.sale_id).filter(
        *filtros, InvuSaleModifier.invu_modifier_id.isnot(None)
    ).group_by(InvuSaleModifier.invu_modifier_id).all()

    recetas, insumos = recetas_insumos or _recetas_de_sucursal(db, branch_id)

    usado: dict = {}
    for tipo, filas in (("item", platos), ("modifier", mods)):
        for source_id, cantidad in filas:
            for linea in recetas.get((tipo, source_id), []):
                item = insumos.get(linea.product_invu_id)
                if not item:
                    continue
                por_unidad = _a_unidad_del_insumo(Decimal(linea.quantity), linea.unit_name, item.unit, item.piece_size, item.grams_per_ml)
                if por_unidad is None:
                    if sin_conversion is not None and cantidad:
                        sin_conversion.add(item.id)
                    continue
                usado[item.id] = usado.get(item.id, Decimal("0")) + Decimal(cantidad or 0) * por_unidad
    return usado



def _insumos_con_receta(db: Session, branch_id: int) -> set:
    """Los insumos que aparecen en alguna receta efectiva de esa sucursal (ver _recetas_de_sucursal)."""
    recetas, insumos = _recetas_de_sucursal(db, branch_id)
    invu_ids = {l.product_invu_id for lineas in recetas.values() for l in lineas}
    return {insumos[i].id for i in invu_ids if i in insumos}
