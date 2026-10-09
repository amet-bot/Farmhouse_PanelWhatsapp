"""
Inventario · Conteos físicos, análisis de diferencias y tablero resumen.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.invu_sales import InvuSyncDay
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem
from models.stock_count import StockCount, StockCountItem
from models.inventory_movement import InventoryMovement
from models.consumption import ConsumptionRecord, ConsumptionItem
from schemas.inventory import (
    StockCountCreate, StockCountItemResponse, StockCountResponse,
    StockCountAnalysis, StockCountAnalysisLine, StockCountAnalysisTotals,
    DashboardBranch, DashboardFigures, DashboardResponse, DashboardTopItem,
)
from services import invu_sales_sync
from services.audit import log_audit_event
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

from .common import logger, router
from .helpers import _dias_utc, _insumos_con_receta, _last_costs_map, _on_hand_map, _recetas_de_sucursal, _uso_por_ventas, _visible_branch_filter



# ==========================================================================
# Conteo físico
# ==========================================================================
def _first_count_ids(db: Session, branch_ids: List[int]) -> set:
    """El id del primer conteo de cada una de esas sucursales: el que hizo de arranque."""
    if not branch_ids:
        return set()
    filas = (
        db.query(func.min(StockCount.id))
        .filter(StockCount.branch_id.in_(branch_ids))
        .group_by(StockCount.branch_id)
        .all()
    )
    return {fila[0] for fila in filas}



def _serialize_count(record: StockCount, is_first: bool) -> StockCountResponse:
    items: List[StockCountItemResponse] = []
    costo = Decimal("0.00")
    has_cost = False
    distintos = 0

    for line in record.items:
        diferencia = Decimal(line.difference)
        if diferencia != 0:
            distintos += 1
            if line.unit_cost is not None:
                costo += diferencia * Decimal(line.unit_cost)
                has_cost = True
        items.append(StockCountItemResponse(
            id=line.id,
            inventory_item_id=line.inventory_item_id,
            item_name=line.inventory_item.name,
            unit=line.inventory_item.unit,
            expected_quantity=line.expected_quantity,
            counted_quantity=line.counted_quantity,
            difference=line.difference,
            unit_cost=line.unit_cost,
        ))

    return StockCountResponse(
        id=record.id,
        branch_id=record.branch_id,
        branch_name=record.branch.name,
        counted_by_user_id=record.counted_by_user_id,
        counted_by_name=record.counted_by_user.name,
        counted_at=record.counted_at,
        notes=record.notes,
        created_at=record.created_at,
        items=items,
        mismatched_count=distintos,
        difference_cost=costo.quantize(Decimal("0.01")) if has_cost else None,
        is_first_count=is_first,
    )



@router.post("/counts", response_model=StockCountResponse, status_code=status.HTTP_201_CREATED)
def create_count(
    count_in: StockCountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Registra un conteo físico: lo que se encontró en el estante.

    Por cada insumo contado se guarda lo que el sistema esperaba, lo que se contó y la
    diferencia, y desde ese momento la existencia de ese insumo es exactamente lo contado. Los
    insumos que no vienen en el conteo no se tocan.

    El primer conteo de una sucursal es su inventario de arranque: la diferencia ahí no es un
    faltante ni un sobrante, es lo que ya había antes de que el sistema llevara la cuenta. La
    respuesta lo marca (`is_first_count`) para que la pantalla lo diga con esas palabras.
    """
    if current_user.role == "agent":
        if count_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar conteos en otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if count_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar conteos en otra sucursal."
            )

    check_target_branch_valid(db, count_in.branch_id)

    item_ids = [line.inventory_item_id for line in count_in.items]
    if len(item_ids) != len(set(item_ids)):
        # Dos renglones del mismo insumo no suman: son dos respuestas distintas a la misma
        # pregunta (cuánto hay) y no hay forma de saber cuál vale.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un insumo aparece dos veces en el conteo. Dejá una sola línea por insumo."
        )

    found_items = db.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).all()
    missing = set(item_ids) - {i.id for i in found_items}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ítem(s) de inventario no encontrados: {sorted(missing)}"
        )

    es_primero = not db.query(StockCount.id).filter(StockCount.branch_id == count_in.branch_id).first()

    # Lo esperado se mira ANTES de grabar, igual que en la merma.
    esperado = _on_hand_map(db, count_in.branch_id, item_ids)
    costos = _last_costs_map(db, count_in.branch_id)

    record = StockCount(
        branch_id=count_in.branch_id,
        counted_by_user_id=current_user.id,
        counted_at=datetime.now(timezone.utc),
        notes=(count_in.notes or None),
    )
    for line in count_in.items:
        antes = esperado.get(line.inventory_item_id, Decimal("0"))
        record.items.append(StockCountItem(
            inventory_item_id=line.inventory_item_id,
            expected_quantity=antes,
            counted_quantity=line.counted_quantity,
            difference=Decimal(line.counted_quantity) - antes,
            unit_cost=costos.get(line.inventory_item_id),
        ))

    db.add(record)
    db.flush()  # asigna record.id antes de generar los movimientos del libro (Fase 4)
    for line in record.items:
        if line.difference == 0:
            continue  # sin diferencia no hay movimiento que registrar
        db.add(InventoryMovement(
            branch_id=record.branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="adjustment",
            quantity=line.difference,
            unit_cost=line.unit_cost,
            occurred_at=record.counted_at,
            source_type="count",
            source_id=record.id,
            created_by_user_id=current_user.id,
        ))
    log_audit_event(
        db, current_user.id, record.branch_id, "count.create", "stock_count", record.id,
        {"items": len(record.items), "is_first_count": es_primero}
    )
    db.commit()
    db.refresh(record)

    respuesta = _serialize_count(record, is_first=es_primero)
    respuesta.analysis = _analizar_conteo(db, record)
    logger.info(
        f"Conteo #{record.id} en sucursal {record.branch_id} por {current_user.name}: "
        f"{len(record.items)} insumos, {respuesta.mismatched_count} con diferencia"
        + (" (arranque)" if es_primero else "")
    )
    return respuesta



# ---- Análisis del conteo: lo que tenía que haber vs. lo que se contó ----
# Hasta este porcentaje la diferencia se toma como que cuadra: balanza, redondeos, lo que queda
# pegado en el recipiente. Por encima es algo que alguien tiene que mirar.
COUNT_TOLERANCE_PCT = Decimal("3")



def _analizar_conteo(db: Session, record: StockCount) -> StockCountAnalysis:
    """
    Explica cada insumo del conteo. Para cada uno se mira su conteo ANTERIOR en la sucursal:
      - Sin conteo anterior → "arranque": es su punto de partida, no un faltante (el sistema
        empezó a contar cuando ya había mercadería).
      - Con conteo anterior → lo que decía el sistema ya incluye entradas, merma y traslados
        desde entonces; falta descontar lo que se cocinó: las ventas de Invu × recetas en ese
        lapso. Lo que queda es lo que nadie registró.
    Se calcula al pedirlo (no se guarda): si las ventas de Invu llegan más tarde, el análisis
    se corrige solo.
    """
    item_ids = [l.inventory_item_id for l in record.items]
    anteriores = dict(
        db.query(StockCountItem.inventory_item_id, func.max(StockCount.id))
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(
            StockCount.branch_id == record.branch_id,
            StockCount.id < record.id,
            StockCountItem.inventory_item_id.in_(item_ids or [0]),
        )
        .group_by(StockCountItem.inventory_item_id)
        .all()
    )
    fechas = dict(db.query(StockCount.id, StockCount.counted_at).filter(StockCount.id.in_(set(anteriores.values()) or [0])).all())
    recetas_insumos = _recetas_de_sucursal(db, record.branch_id)
    sin_conversion: dict = {cid: set() for cid in set(anteriores.values())}
    usos = {
        cid: _uso_por_ventas(db, record.branch_id, fechas[cid], record.counted_at,
                             sin_conversion=sin_conversion[cid], recetas_insumos=recetas_insumos)
        for cid in set(anteriores.values())
    }
    # Un insumo con consumo anotado a mano en ese lapso ya lo trae descontado en lo que decía el
    # sistema: estimarle además el uso por ventas lo restaría dos veces (misma regla que
    # `_vendido_desde_conteo`, que es la que muestra la existencia).
    con_manual = {
        cid: {
            r[0] for r in db.query(ConsumptionItem.inventory_item_id)
            .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
            .filter(
                ConsumptionRecord.branch_id == record.branch_id,
                ConsumptionItem.inventory_item_id.in_(item_ids or [0]),
                ConsumptionRecord.occurred_at > fechas[cid], ConsumptionRecord.occurred_at <= record.counted_at,
            ).distinct().all()
        }
        for cid in set(anteriores.values())
    }
    con_receta = _insumos_con_receta(db, record.branch_id)

    totales = StockCountAnalysisTotals(items=len(record.items))
    lineas: List[StockCountAnalysisLine] = []
    for line in record.items:
        item = line.inventory_item
        contado = Decimal(line.counted_quantity)
        sistema = Decimal(line.expected_quantity)
        costo = Decimal(line.unit_cost) if line.unit_cost is not None and Decimal(line.unit_cost) > 0 else None
        estimado = False
        if costo is None and item.effective_cost is not None:
            costo, estimado = Decimal(item.effective_cost), True

        previo = anteriores.get(item.id)
        if previo is None:
            valor = (contado * costo) if costo is not None else None
            totales.baseline += 1
            if valor:
                totales.baseline_value += valor
                totales.cost_estimated = totales.cost_estimated or estimado
            lineas.append(StockCountAnalysisLine(
                inventory_item_id=item.id, name=item.name, unit=item.unit, status="arranque",
                expected_records=sistema, expected=sistema, counted=contado,
                unit_cost=costo, cost=(valor.quantize(Decimal("0.01")) if valor is not None else None),
                cost_estimated=estimado,
            ))
            continue

        tiene_receta = item.id in con_receta
        usado = usos.get(previo, {}).get(item.id, Decimal("0")) if tiene_receta else None
        if usado is not None and item.id in con_manual.get(previo, ()):
            usado = Decimal("0")
        esperado = sistema - (usado or Decimal("0"))
        sin_explicar = contado - esperado
        base = max(abs(esperado), abs(contado))
        pct = (sin_explicar / base * 100) if base > 0 else Decimal("0")
        if abs(sin_explicar) < Decimal("0.001") or abs(pct) <= COUNT_TOLERANCE_PCT:
            estado = "cuadra"
        elif sin_explicar > 0:
            estado = "sobra"
        elif not tiene_receta:
            estado = "sin_receta"
        elif item.id in sin_conversion.get(previo, ()):
            # Parte de lo que se usó no se pudo convertir (receta en gramos, insumo por unidad y
            # sin peso por pieza): el faltante puede ser solo eso. No se acusa de pérdida.
            estado = "sin_conversion"
        else:
            estado = "falta"
        valor = (sin_explicar * costo) if costo is not None else None

        if estado == "cuadra":
            totales.ok += 1
        elif estado == "sobra":
            totales.surplus += 1
            totales.surplus_cost += valor or Decimal("0")
        elif estado == "falta":
            totales.missing += 1
            totales.missing_cost += -(valor or Decimal("0"))
        elif estado == "sin_conversion":
            totales.no_conversion += 1
            totales.no_conversion_cost += -(valor or Decimal("0"))
        else:
            totales.no_recipe += 1
            totales.no_recipe_cost += -(valor or Decimal("0"))
        if valor and estado != "cuadra":
            totales.cost_estimated = totales.cost_estimated or estimado

        lineas.append(StockCountAnalysisLine(
            inventory_item_id=item.id, name=item.name, unit=item.unit, status=estado,
            expected_records=sistema,
            used_by_sales=(usado.quantize(Decimal("0.001")) if usado is not None else None),
            expected=esperado.quantize(Decimal("0.001")), counted=contado,
            unexplained=sin_explicar.quantize(Decimal("0.001")), unexplained_pct=pct.quantize(Decimal("0.1")),
            unit_cost=costo, cost=(valor.quantize(Decimal("0.01")) if valor is not None else None),
            cost_estimated=estimado, since=fechas.get(previo),
        ))

    # Primero lo que hay que ir a mirar (lo que más plata falta), al final lo que cuadró.
    orden = {"falta": 0, "sin_receta": 1, "sin_conversion": 2, "sobra": 3, "cuadra": 4, "arranque": 5}
    lineas.sort(key=lambda l: (orden[l.status], -abs(l.cost or Decimal("0")), l.name))
    for campo in ("missing_cost", "surplus_cost", "no_recipe_cost", "no_conversion_cost", "baseline_value"):
        setattr(totales, campo, getattr(totales, campo).quantize(Decimal("0.01")))

    hoy = db.query(InvuSyncDay.synced_at).filter(
        InvuSyncDay.branch_id == record.branch_id,
        InvuSyncDay.business_date == invu_sales_sync.hoy_panama(),
    ).first()
    return StockCountAnalysis(
        count_id=record.id, branch_id=record.branch_id, branch_name=record.branch.name,
        counted_at=record.counted_at, tolerance_pct=COUNT_TOLERANCE_PCT,
        recipes_available=bool(con_receta), sales_synced_at=(hoy[0] if hoy else None),
        totals=totales, lines=lineas,
    )



# ==========================================================================
# Tablero del Resumen: ventas, compras, merma y faltantes
# ==========================================================================
def _pct_de(parte: Decimal, total: Optional[Decimal]) -> Optional[Decimal]:
    return (parte / total * 100).quantize(Decimal("0.1")) if total else None



def _cifras_sucursal(db: Session, branch: Branch, desde: date, hasta: date, tops: Optional[dict] = None) -> DashboardFigures:
    """Las cifras de una sucursal en [desde, hasta] (días de Panamá). `tops` junta merma y faltantes por insumo."""
    ini, fin = _dias_utc(desde, hasta)
    f = DashboardFigures()

    # La venta del día que da Invu en su reporte (igual que la pantalla de Ventas), y la nuestra
    # solo si Invu no la dio.
    venta = db.query(func.sum(func.coalesce(InvuSyncDay.invu_total, InvuSyncDay.net_total))).filter(
        InvuSyncDay.branch_id == branch.id, InvuSyncDay.business_date >= desde,
        InvuSyncDay.business_date <= hasta, InvuSyncDay.net_total.isnot(None),
    ).scalar()
    f.sales_net = Decimal(venta).quantize(Decimal("0.01")) if venta is not None else None

    compras = db.query(
        func.coalesce(func.sum(ShipmentItem.quantity * ShipmentItem.unit_cost), 0),
        func.sum(case((ShipmentItem.unit_cost.is_(None), 1), else_=0)),
    ).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
        Shipment.branch_id == branch.id, Shipment.received_at >= ini, Shipment.received_at < fin,
    ).one()
    f.purchases = Decimal(compras[0] or 0).quantize(Decimal("0.01"))
    f.purchase_lines_without_cost = int(compras[1] or 0)

    mermas = db.query(
        WasteItem.inventory_item_id, InventoryItem.name, InventoryItem.unit,
        func.sum(WasteItem.quantity),
        func.sum(WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.effective_cost, 0)),
        func.sum(case((WasteItem.unit_cost.is_(None), 1), else_=0)),
    ).join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id).join(
        InventoryItem, InventoryItem.id == WasteItem.inventory_item_id
    ).filter(
        WasteRecord.branch_id == branch.id, WasteRecord.occurred_at >= ini, WasteRecord.occurred_at < fin,
    ).group_by(WasteItem.inventory_item_id, InventoryItem.name, InventoryItem.unit).all()
    for iid, nombre, unidad, cant, costo, sin_costo in mermas:
        f.waste += Decimal(costo or 0)
        f.waste_estimated = f.waste_estimated or bool(sin_costo)
        if tops is not None:
            t = tops["waste"].setdefault(iid, DashboardTopItem(inventory_item_id=iid, name=nombre, unit=unidad,
                                                               quantity=Decimal("0"), cost=Decimal("0")))
            t.quantity += Decimal(cant or 0)
            t.cost += Decimal(costo or 0)
            t.estimated = t.estimated or bool(sin_costo)
    f.waste = f.waste.quantize(Decimal("0.01"))

    conteos = db.query(StockCount).options(
        joinedload(StockCount.items).joinedload(StockCountItem.inventory_item), joinedload(StockCount.branch),
    ).filter(StockCount.branch_id == branch.id, StockCount.counted_at >= ini, StockCount.counted_at < fin).all()
    f.counts = len(conteos)
    for c in conteos:
        a = _analizar_conteo(db, c)
        f.count_missing += a.totals.missing_cost
        f.count_no_recipe += a.totals.no_recipe_cost + a.totals.no_conversion_cost
        f.count_surplus += a.totals.surplus_cost
        if tops is not None:
            for l in a.lines:
                if l.status not in ("falta", "sin_receta", "sin_conversion") or l.unexplained is None:
                    continue
                t = tops["missing"].setdefault(l.inventory_item_id, DashboardTopItem(
                    inventory_item_id=l.inventory_item_id, name=l.name, unit=l.unit,
                    quantity=Decimal("0"), cost=Decimal("0")))
                t.quantity += abs(l.unexplained)
                t.cost += abs(l.cost or Decimal("0"))
                t.estimated = t.estimated or l.cost_estimated

    return f



def _sumar_cifras(filas: List[DashboardFigures]) -> DashboardFigures:
    t = DashboardFigures()
    ventas = [f.sales_net for f in filas if f.sales_net is not None]
    t.sales_net = sum(ventas, Decimal("0")) if ventas else None
    for campo in ("purchases", "waste", "count_missing", "count_no_recipe", "count_surplus"):
        setattr(t, campo, sum((getattr(f, campo) for f in filas), Decimal("0")))
    t.purchase_lines_without_cost = sum(f.purchase_lines_without_cost for f in filas)
    t.counts = sum(f.counts for f in filas)
    t.waste_estimated = any(f.waste_estimated for f in filas)
    return t



def _porcentajes(f: DashboardFigures) -> DashboardFigures:
    f.waste_pct_sales = _pct_de(f.waste, f.sales_net)
    f.purchases_pct_sales = _pct_de(f.purchases, f.sales_net)
    return f



@router.get("/dashboard", response_model=DashboardResponse)
def inventory_dashboard(
    days: int = Query(7, ge=1, le=90),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    El tablero del Resumen: por sucursal y en total, en los últimos `days` días (Panamá) contra
    los `days` anteriores. Ventas (Invu), compras (cargamentos), merma y lo que faltó en los
    conteos (descontando lo vendido).

    Hubo un "costo de lo vendido" / "cobertura de recetas" acá (cruzaba las ventas con las
    recetas de Invu). Se quitó el 2026-10-01: con tan pocas recetas cargadas en Invu (20-25% de
    cobertura real) el número salía muy por debajo del costo real y el food cost % que mostraba
    (4-5%) era engañoso. El día que la cobertura de recetas en Invu esté completa, se puede
    volver a agregar — ver el historial de este archivo para la implementación original.
    """
    hasta = invu_sales_sync.hoy_panama()
    desde = hasta - timedelta(days=days - 1)
    prev_hasta = desde - timedelta(days=1)
    prev_desde = prev_hasta - timedelta(days=days - 1)

    efectiva = _visible_branch_filter(current_user, branch_id)
    consulta = db.query(Branch).filter(Branch.active == True)  # noqa: E712
    if efectiva is not None:
        consulta = consulta.filter(Branch.id == efectiva)

    tops = {"waste": {}, "missing": {}}
    filas: List[DashboardBranch] = []
    previas: List[DashboardFigures] = []
    for branch in consulta.order_by(Branch.id).all():
        actual = _cifras_sucursal(db, branch, desde, hasta, tops)
        anterior = _cifras_sucursal(db, branch, prev_desde, prev_hasta)
        # Una sucursal sin nada (Catering, una recién creada) no suma una fila vacía al tablero.
        if efectiva is None and actual.sales_net is None and not any(
            (actual.purchases, actual.waste, actual.counts, anterior.sales_net)
        ):
            continue
        filas.append(DashboardBranch(branch_id=branch.id, branch_code=branch.code, branch_name=branch.name,
                                     **_porcentajes(actual).model_dump()))
        previas.append(anterior)

    def top(d: dict) -> List[DashboardTopItem]:
        lista = sorted(d.values(), key=lambda t: t.cost, reverse=True)[:5]
        for t in lista:
            t.cost = t.cost.quantize(Decimal("0.01"))
            t.quantity = t.quantity.quantize(Decimal("0.001"))
        return [t for t in lista if t.cost > 0]

    return DashboardResponse(
        date_from=desde, date_to=hasta, prev_from=prev_desde, prev_to=prev_hasta, branch_id=efectiva,
        totals=_porcentajes(_sumar_cifras(filas)),
        prev_totals=_porcentajes(_sumar_cifras(previas)),
        branches=filas,
        top_waste=top(tops["waste"]), top_missing=top(tops["missing"]),
    )



@router.get("/counts/{count_id}/analysis", response_model=StockCountAnalysis)
def count_analysis(
    count_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    record = db.query(StockCount).options(
        joinedload(StockCount.items).joinedload(StockCountItem.inventory_item),
        joinedload(StockCount.branch),
    ).filter(StockCount.id == count_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conteo no encontrado.")
    efectiva = _visible_branch_filter(current_user, record.branch_id)
    if efectiva is not None and efectiva != record.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los conteos de otra sucursal.")
    return _analizar_conteo(db, record)



@router.get("/counts", response_model=List[StockCountResponse])
def list_counts(
    branch_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(StockCount).options(
        joinedload(StockCount.items).joinedload(StockCountItem.inventory_item),
        joinedload(StockCount.branch),
        joinedload(StockCount.counted_by_user),
    )

    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(StockCount.branch_id == efectiva)

    records = query.order_by(StockCount.counted_at.desc(), StockCount.id.desc()).offset(offset).limit(limit).all()
    primeros = _first_count_ids(db, list({r.branch_id for r in records}))
    return [_serialize_count(r, is_first=(r.id in primeros)) for r in records]
