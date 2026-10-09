"""
Inventario · Merma: registro, fotos, análisis, cruce con recetas de Invu.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload, selectinload

from database import SessionLocal, get_db
from models.inventory_item import InventoryItem, KIND_HOUSE, KIND_RAW
from models.invu_sales import InvuRecipeLine, InvuSaleLine, InvuSaleModifier, InvuSyncDay
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem, WastePhoto
from models.stock_count import StockCount, StockCountItem
from models.inventory_movement import InventoryMovement
from schemas.inventory import (
    InventoryItemDensity, InventoryItemPieceSize, InventoryItemResponse,
    WasteCreate, WasteItemResponse, WastePhotoResponse, WasteReasonResponse, WasteResponse,
    WasteInsightItem, WasteInsights,
    WasteAnalyticsGroup, WasteAnalyticsItem, WasteAnalyticsDay, WasteAnalyticsResponse, WasteAnalyticsTotals, WasteAnalyticsYield,
    WasteRecipeDish, WasteRecipeDishShare, WasteRecipeUsageItem, WasteRecipeUsageResponse,
)
from config import settings
from services import invu_client, invu_recipes_sync, invu_sales_sync, push_service
from services.audit import log_audit_event
from security.auth import get_current_authorized_user
from security.permissions import has_permission, require_permission
from security.access_control import check_target_branch_valid

from .common import PROCESS_WASTE_REASONS, WASTE_REASONS, WASTE_REASON_LABELS, logger, router
from .helpers import _KG_POR_UNIDAD, _a_unidad_del_insumo, _chequear_quien_borra, _chequear_sin_conteo_posterior, _dias_utc, _existencia_map, _familia_de_unidad, _hay_a_quien_avisar, _insumos_con_receta, _uso_por_ventas, _visible_branch_filter



def _last_known_cost(db: Session, branch_id: int, inventory_item_id: int) -> Optional[Decimal]:
    """
    Cuánto costaba la última vez que ese insumo entró a esa sucursal.

    Se busca en esa sucursal y no en todas: el mismo tomate puede costar distinto en Clayton y
    en Costa del Este según el proveedor de cada una, y valuar la merma con el precio ajeno
    inventaría una pérdida que no fue.
    """
    row = (
        db.query(ShipmentItem.unit_cost)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(
            Shipment.branch_id == branch_id,
            ShipmentItem.inventory_item_id == inventory_item_id,
            ShipmentItem.unit_cost.isnot(None),
        )
        .order_by(Shipment.received_at.desc(), ShipmentItem.id.desc())
        .first()
    )
    return row[0] if row else None



def _serialize_waste(record: WasteRecord, stock_before: Optional[dict] = None) -> WasteResponse:
    items: List[WasteItemResponse] = []
    total_cost = Decimal("0.00")
    has_cost = False
    negativos: List[str] = []
    # Lo que se muestra: el costo de cargamento y, donde no hay, el de referencia de Invu (el mismo
    # criterio del Análisis). Sin esto la lista decía "—" aunque el formulario estimó $5.61.
    display = Decimal("0.00")
    display_has = False
    estimado = False

    for line in record.items:
        if line.unit_cost is not None:
            total_cost += (Decimal(line.quantity) * Decimal(line.unit_cost))
            has_cost = True
            display += Decimal(line.quantity) * Decimal(line.unit_cost)
            display_has = True
        elif line.inventory_item.effective_cost is not None:
            display += Decimal(line.quantity) * Decimal(line.inventory_item.effective_cost)
            display_has = True
            estimado = True

        previo = None
        if stock_before is not None:
            previo = stock_before.get(line.inventory_item_id)
            if previo is not None and previo < Decimal(line.quantity):
                negativos.append(line.inventory_item.name)

        items.append(WasteItemResponse(
            id=line.id,
            inventory_item_id=line.inventory_item_id,
            item_name=line.inventory_item.name,
            unit=line.inventory_item.unit,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            reference_cost=(line.inventory_item.effective_cost if line.unit_cost is None else None),
            mode=line.mode,
            pieces=line.pieces,
            piece_size=line.inventory_item.piece_size,
            measured_amount=line.measured_amount,
            weight_estimated=(line.mode == "entera" and line.measured_amount is None),
            stock_before=previo,
        ))

    return WasteResponse(
        id=record.id,
        branch_id=record.branch_id,
        branch_name=record.branch.name,
        recorded_by_user_id=record.recorded_by_user_id,
        recorded_by_name=record.recorded_by_user.name,
        occurred_at=record.occurred_at,
        reason=record.reason,
        reason_label=WASTE_REASON_LABELS.get(record.reason, record.reason),
        notes=record.notes,
        created_at=record.created_at,
        items=items,
        total_cost=total_cost.quantize(Decimal("0.01")) if has_cost else None,
        display_cost=display.quantize(Decimal("0.01")) if display_has else None,
        cost_estimated=estimado,
        negative_items=negativos,
        weight_value=record.weight_value,
        weight_unit=record.weight_unit,
        weight_estimated=bool(record.weight_estimated),
        is_process=record.reason in PROCESS_WASTE_REASONS,
        processed_value=record.processed_value,
        processed_unit=record.processed_unit,
        yield_pct=_rendimiento(record),
        photos=[
            WastePhotoResponse(
                id=p.id, content_type=p.content_type, size_bytes=p.size_bytes,
                uploaded_by_name=(p.uploaded_by_user.name if p.uploaded_by_user else None),
                created_at=p.created_at,
            )
            for p in record.photos
        ],
    )



@router.get("/waste/reasons", response_model=List[WasteReasonResponse])
def list_waste_reasons(current_user: User = Depends(get_current_authorized_user)):
    """
    Los motivos vienen del servidor y no escritos en el frontend: el día que el negocio agregue
    uno, se agrega en un solo lugar y las pantallas y los reportes ya hablan el mismo idioma.
    """
    return [WasteReasonResponse(code=code, label=label) for code, label in WASTE_REASONS]



def _cantidad_de_linea(item: InventoryItem, line, piece_size: Optional[Decimal]) -> Decimal:
    """
    La cantidad en la unidad del insumo de una línea de merma (ver WasteItemCreate). Falla con
    400 y un mensaje que dice qué falta, con el nombre del insumo, en vez de guardar un número
    inventado.
    """
    def falta(msg: str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{item.name}: {msg}")

    if line.mode is None:
        if line.quantity is None:
            falta("falta la cantidad.")
        return Decimal(line.quantity)

    familia, base = _familia_de_unidad(item.unit)
    medida = "cuánto trae (ml)" if familia == "volumen" else "cuánto pesa (g)"
    if line.mode == "entera":
        if line.pieces is None:
            falta("falta cuántas piezas enteras se botaron.")
        if familia == "unidad":
            cantidad = Decimal(line.pieces)
        elif line.measured_amount is not None:
            # Se pesó: manda la balanza, no el promedio de la pieza.
            cantidad = Decimal(line.measured_amount) / base
        else:
            if not piece_size:
                falta(f"falta {medida} una pieza entera (o el peso real, si se pesó).")
            cantidad = Decimal(line.pieces) * Decimal(piece_size) / base
    else:  # parte
        if familia == "unidad":
            if line.part_amount is None:
                falta("falta cuánto pesa (g) lo que se botó.")
            if not piece_size:
                falta("falta cuánto pesa (g) una pieza entera, para saber qué parte es.")
            cantidad = Decimal(line.part_amount) / Decimal(piece_size)
        elif line.part_amount is not None:
            cantidad = Decimal(line.part_amount) / base
        elif line.quantity is not None:
            cantidad = Decimal(line.quantity)
        else:
            falta("falta cuánto pesa lo que se botó.")

    cantidad = cantidad.quantize(Decimal("0.001"))
    if cantidad <= 0:
        falta("la cantidad da cero; revisá el peso.")
    return cantidad



@router.patch("/items/{item_id}/piece-size", response_model=InventoryItemResponse)
def set_item_piece_size(
    item_id: int,
    body: InventoryItemPieceSize,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Cuánto es una pieza entera de un insumo (g, o ml si es de volumen). Supervisor o admin."""
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insumo no encontrado.")
    antes = item.piece_size
    item.piece_size = body.piece_size
    log_audit_event(db, current_user.id, None, "item.piece_size", "inventory_item", item.id,
                    {"before": str(antes) if antes is not None else None,
                     "after": str(body.piece_size) if body.piece_size is not None else None})
    db.commit()
    db.refresh(item)
    return item



@router.patch("/items/{item_id}/density", response_model=InventoryItemResponse)
def set_item_density(
    item_id: int,
    body: InventoryItemDensity,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Cuántos gramos pesa 1 ml del insumo, para las recetas que lo piden en la otra unidad."""
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insumo no encontrado.")
    antes = item.grams_per_ml
    item.grams_per_ml = body.grams_per_ml
    log_audit_event(db, current_user.id, None, "item.grams_per_ml", "inventory_item", item.id,
                    {"before": str(antes) if antes is not None else None,
                     "after": str(body.grams_per_ml) if body.grams_per_ml is not None else None})
    db.commit()
    db.refresh(item)
    return item



# Cuándo una merma merece que se entere el encargado sin tener que ir a buscarla: cuando cuesta
# mucho de una vez o cuando el mismo insumo se bota por el mismo motivo varias veces en el mes.
# El residuo al limpiar queda fuera: es merma esperada y se mide con el rendimiento.
WASTE_ALERT_MIN_COST = Decimal("20")

WASTE_ALERT_REPEAT = 3



def _alertas_de_merma(record: WasteRecord, insights: WasteInsights) -> List[str]:
    """Las razones para avisar, en frases cortas (van en la notificación y en la pantalla)."""
    if record.reason in PROCESS_WASTE_REASONS:
        return []
    razones: List[str] = []
    total = sum((i.this_cost or Decimal("0") for i in insights.items), Decimal("0"))
    if total >= WASTE_ALERT_MIN_COST:
        razones.append(f"Se pierden ${total.quantize(Decimal('0.01'))} de una vez")
    motivo = WASTE_REASON_LABELS.get(record.reason, record.reason).lower()
    for i in insights.items:
        if i.same_reason_month >= WASTE_ALERT_REPEAT:
            razones.append(f"{i.name}: {i.same_reason_month}.ª vez en el mes por «{motivo}»")
    return razones



def _avisar_merma_background(branch_id: int, title: str, body: str, url: str, tag: str) -> None:
    db = SessionLocal()
    try:
        push_service.notify_branch_staff(db, branch_id, title, body, url, tag=tag, managers_only=True)
    except Exception as e:  # un aviso fallido no deshace la merma
        logger.error(f"[Push merma] sucursal {branch_id}: {e}", exc_info=True)
    finally:
        db.close()



@router.post("/waste", response_model=WasteResponse, status_code=status.HTTP_201_CREATED)
def create_waste(
    waste_in: WasteCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Registra una merma. **No bloquea** cuando la cantidad supera la existencia calculada.

    El sistema empezó a registrar entradas hace poco y nadie cargó el inventario de arranque de
    cada sucursal, así que el stock calculado nace más bajo que el real. Bloquear haría el
    módulo inusable justo cuando más se lo necesita. Se guarda, la existencia queda en negativo
    y la respuesta marca cuáles insumos quedaron así (`negative_items`), que es la señal de que
    falta cargar el arranque — no de que alguien se equivocó.
    """
    if current_user.role == "agent":
        if waste_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar mermas en otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if waste_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar mermas en otra sucursal."
            )

    check_target_branch_valid(db, waste_in.branch_id)
    _chequear_fecha_posterior_a_conteo(
        db, waste_in.branch_id, [l.inventory_item_id for l in waste_in.items],
        waste_in.occurred_at or datetime.now(timezone.utc))

    if waste_in.reason not in WASTE_REASON_LABELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Motivo de merma no válido."
        )

    item_ids = [line.inventory_item_id for line in waste_in.items]
    found_items = db.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).all()
    missing = set(item_ids) - {i.id for i in found_items}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ítem(s) de inventario no encontrados: {sorted(missing)}"
        )

    # Cuánto es cada línea en la unidad del insumo. El tamaño de una pieza que se escribe en el
    # formulario se aprende para el insumo si todavía no tenía; cambiar uno ya guardado es de
    # supervisor o admin (si no, vale el guardado, que es lo que la pantalla le mostró fijo).
    por_id = {i.id: i for i in found_items}
    puede_ajustar = has_permission(current_user, "inventory.adjust")
    cantidades = []
    for line in waste_in.items:
        item = por_id[line.inventory_item_id]
        tamano = item.piece_size
        if (tamano is None and line.piece_size is None and line.mode == "entera"
                and line.measured_amount is not None and line.pieces):
            # Nadie había dicho cuánto pesa una pieza, pero esta vez se pesaron: el promedio de
            # lo pesado queda como peso de la pieza para la próxima (se corrige desde Insumos).
            item.piece_size = tamano = (Decimal(line.measured_amount) / Decimal(line.pieces)).quantize(Decimal("0.001"))
            log_audit_event(db, current_user.id, waste_in.branch_id, "item.piece_size", "inventory_item", item.id,
                            {"before": None, "after": str(tamano), "via": "waste_measured"})
        if line.piece_size is not None and (tamano is None or puede_ajustar):
            if tamano != line.piece_size:
                log_audit_event(db, current_user.id, waste_in.branch_id, "item.piece_size", "inventory_item", item.id,
                                {"before": str(tamano) if tamano is not None else None,
                                 "after": str(line.piece_size), "via": "waste"})
            item.piece_size = tamano = line.piece_size
        cantidades.append(_cantidad_de_linea(item, line, tamano))

    # La existencia se mira ANTES de grabar: después este mismo registro ya estaría restando.
    stock_before = _existencia_map(db, waste_in.branch_id, item_ids)

    record = WasteRecord(
        branch_id=waste_in.branch_id,
        recorded_by_user_id=current_user.id,
        occurred_at=waste_in.occurred_at or datetime.now(timezone.utc),
        reason=waste_in.reason,
        notes=(waste_in.notes or None),
        weight_value=waste_in.weight_value,
        weight_unit=((waste_in.weight_unit or "kg") if waste_in.weight_value is not None else None),
        weight_estimated=(bool(waste_in.weight_estimated) if waste_in.weight_value is not None else None),
    )
    if waste_in.reason in PROCESS_WASTE_REASONS and waste_in.processed_value is not None:
        record.processed_value = waste_in.processed_value
        record.processed_unit = waste_in.processed_unit or "kg"
    for line, cantidad in zip(waste_in.items, cantidades):
        costo = line.unit_cost
        if costo is None:
            costo = _last_known_cost(db, waste_in.branch_id, line.inventory_item_id)
        record.items.append(WasteItem(
            inventory_item_id=line.inventory_item_id,
            quantity=cantidad,
            unit_cost=costo,
            mode=line.mode,
            pieces=(line.pieces if line.mode == "entera" else None),
            # Lo pesado de la línea: el peso real de una "entera" o el pedazo de un insumo por
            # unidad (en uno de peso, la cantidad ya es lo que marcó la balanza).
            measured_amount=(line.measured_amount if line.mode == "entera"
                             else line.part_amount if line.mode == "parte" else None),
        ))

    db.add(record)
    db.flush()  # asigna record.id antes de generar los movimientos del libro (Fase 4)
    for line in record.items:
        db.add(InventoryMovement(
            branch_id=record.branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="out",
            quantity=-Decimal(line.quantity),
            unit_cost=line.unit_cost,
            occurred_at=record.occurred_at,
            source_type="waste",
            source_id=record.id,
            created_by_user_id=current_user.id,
        ))
    log_audit_event(
        db, current_user.id, record.branch_id, "waste.create", "waste_record", record.id,
        {"reason": record.reason, "items": len(record.items)}
    )
    db.commit()
    db.refresh(record)

    respuesta = _serialize_waste(record, stock_before=stock_before)
    respuesta.insights = _waste_insights(db, record)
    razones = _alertas_de_merma(record, respuesta.insights)
    if razones:
        respuesta.alert_reasons = razones
        respuesta.notified = _hay_a_quien_avisar(db, record.branch_id)
        insumos = ", ".join(i.name for i in respuesta.insights.items[:3])
        background_tasks.add_task(
            _avisar_merma_background, record.branch_id,
            f"Merma importante · {record.branch.name}",
            f"{insumos} ({WASTE_REASON_LABELS.get(record.reason, record.reason)}). " + "; ".join(razones),
            f"/merma?waste={record.id}", f"fh-waste-{record.id}",
        )
    logger.info(
        f"Merma #{record.id} ({record.reason}) en sucursal {record.branch_id} por {current_user.name}"
        + (f" — deja en negativo: {', '.join(respuesta.negative_items)}" if respuesta.negative_items else "")
    )
    return respuesta



def _waste_insights(db: Session, record: WasteRecord) -> WasteInsights:
    """
    Pone la merma en contexto, en su sucursal y tomando como "hoy" el día en que ocurrió (así el
    detalle de una merma vieja dice lo mismo que dijo al cargarla): semana contra la anterior, el
    mes, si el mismo motivo se repite, en qué puesto está el insumo y, con recetas de Invu, qué
    parte de lo que se usó terminó en la basura.
    """
    tz = invu_sales_sync.PANAMA_TZ
    ocurrio = record.occurred_at if record.occurred_at.tzinfo else record.occurred_at.replace(tzinfo=timezone.utc)
    dia = ocurrio.astimezone(tz).date()
    semana = _dias_utc(dia - timedelta(days=6), dia)
    previa = _dias_utc(dia - timedelta(days=13), dia - timedelta(days=7))
    mes = _dias_utc(dia - timedelta(days=29), dia)

    costo_linea = WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.effective_cost, 0)

    def por_insumo(rango, extra=()):
        filas = db.query(
            WasteItem.inventory_item_id,
            func.coalesce(func.sum(WasteItem.quantity), 0),
            func.coalesce(func.sum(costo_linea), 0),
            func.count(func.distinct(WasteRecord.id)),
        ).join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id).join(
            InventoryItem, InventoryItem.id == WasteItem.inventory_item_id
        ).filter(
            WasteRecord.branch_id == record.branch_id,
            WasteRecord.occurred_at >= rango[0], WasteRecord.occurred_at < rango[1],
            *extra,
        ).group_by(WasteItem.inventory_item_id).all()
        return {r[0]: (Decimal(r[1]), Decimal(r[2]), r[3]) for r in filas}

    sem = por_insumo(semana)
    prev = por_insumo(previa)
    mensual = por_insumo(mes)
    mismo_motivo = por_insumo(mes, (WasteRecord.reason == record.reason,))
    ranking = [iid for iid, _ in sorted(mensual.items(), key=lambda kv: kv[1][1], reverse=True) if mensual[iid][1] > 0]
    uso = _uso_por_ventas(db, record.branch_id, mes[0], mes[1])
    con_receta = _insumos_con_receta(db, record.branch_id)

    def total_sucursal(rango):
        fila = db.query(func.coalesce(func.sum(costo_linea), 0), func.count(func.distinct(WasteRecord.id))).select_from(WasteItem).join(
            WasteRecord, WasteRecord.id == WasteItem.waste_record_id
        ).join(InventoryItem, InventoryItem.id == WasteItem.inventory_item_id).filter(
            WasteRecord.branch_id == record.branch_id,
            WasteRecord.occurred_at >= rango[0], WasteRecord.occurred_at < rango[1],
        ).one()
        return Decimal(fila[0]), fila[1]

    sem_total, sem_registros = total_sucursal(semana)
    prev_total, _ = total_sucursal(previa)
    vencido = record.reason == "vencido"
    ocurrio_utc = ocurrio.astimezone(timezone.utc).replace(tzinfo=None)

    q2 = lambda v: Decimal(v).quantize(Decimal("0.01"))  # noqa: E731
    items: List[WasteInsightItem] = []
    for line in record.items:
        item = line.inventory_item
        costo = line.unit_cost if line.unit_cost is not None else item.effective_cost
        s = sem.get(item.id, (Decimal("0"), Decimal("0"), 0))
        m = mensual.get(item.id, (Decimal("0"), Decimal("0"), 0))
        usado = uso.get(item.id) if item.id in con_receta else None
        items.append(WasteInsightItem(
            inventory_item_id=item.id, name=item.name, unit=item.unit,
            this_quantity=line.quantity,
            this_cost=(q2(Decimal(line.quantity) * Decimal(costo)) if costo is not None else None),
            week_quantity=s[0].quantize(Decimal("0.001")), week_cost=q2(s[1]), week_records=s[2],
            prev_week_cost=q2(prev.get(item.id, (0, Decimal("0"), 0))[1]),
            month_cost=q2(m[1]), month_records=m[2],
            same_reason_month=mismo_motivo.get(item.id, (0, 0, 0))[2],
            rank_month=(ranking.index(item.id) + 1 if item.id in ranking else None),
            items_ranked=len(ranking),
            used_month=(usado.quantize(Decimal("0.001")) if usado is not None else None),
            waste_pct_month=((m[0] / (usado + m[0]) * 100).quantize(Decimal("0.1")) if usado else None),
            cost_estimated=line.unit_cost is None and item.effective_cost is not None,
            **(_compra_contra_vencimiento(db, record, item, usado, ocurrio_utc) if vencido else {}),
        ))

    return WasteInsights(
        waste_id=record.id, branch_id=record.branch_id, branch_name=record.branch.name,
        reason=record.reason, reason_label=WASTE_REASON_LABELS.get(record.reason, record.reason),
        branch_week_cost=q2(sem_total), branch_prev_week_cost=q2(prev_total), branch_week_records=sem_registros,
        items=items,
    )



def _compra_contra_vencimiento(db: Session, record: WasteRecord, item: InventoryItem,
                               usado_mes: Optional[Decimal], ocurrio_utc: datetime) -> dict:
    """
    Cuando algo se vence: ¿se compró de más? Se mira la última compra de ese insumo en la
    sucursal antes del vencimiento y, con recetas, cuánto se usa por día (30 días de ventas):

      alcanzaba para  = compra / uso por día
      duró            = días entre la compra y el vencimiento
      máximo sugerido = uso por día × días que duró   (lo que se alcanza a usar antes de vencer)

    Sin receta no se sabe el ritmo de uso: se informa la compra y cuánto duró, sin sugerir.
    """
    compra = db.query(ShipmentItem, Shipment).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
        Shipment.branch_id == record.branch_id,
        ShipmentItem.inventory_item_id == item.id,
        ShipmentItem.quantity > 0,
        Shipment.received_at <= ocurrio_utc,
    ).order_by(Shipment.received_at.desc(), Shipment.id.desc()).first()
    vencidos = db.query(func.count(func.distinct(WasteRecord.id))).join(
        WasteItem, WasteItem.waste_record_id == WasteRecord.id
    ).filter(
        WasteRecord.branch_id == record.branch_id, WasteRecord.reason == "vencido",
        WasteItem.inventory_item_id == item.id,
        WasteRecord.occurred_at > ocurrio_utc - timedelta(days=90), WasteRecord.occurred_at <= ocurrio_utc,
    ).scalar() or 0
    datos: dict = {"expired_90d": int(vencidos)}
    if not compra:
        return datos
    linea, envio = compra
    cantidad = Decimal(linea.quantity)
    dias = max((ocurrio_utc - envio.received_at).days, 0)
    datos.update(
        last_purchase_qty=cantidad, last_purchase_at=envio.received_at,
        last_purchase_supplier=(envio.supplier.name if envio.supplier else None),
        days_to_expire=dias,
    )
    if usado_mes and usado_mes > 0:
        por_dia = usado_mes / 30
        datos["used_per_day"] = por_dia.quantize(Decimal("0.001"))
        datos["purchase_cover_days"] = (cantidad / por_dia).quantize(Decimal("0.1"))
        if dias > 0:
            datos["suggested_max_qty"] = (por_dia * dias).quantize(Decimal("0.001"))
    return datos



@router.get("/waste/{waste_id}/insights", response_model=WasteInsights)
def waste_insights(
    waste_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    return _waste_insights(db, _waste_for_user(db, waste_id, current_user))



@router.get("/waste", response_model=List[WasteResponse])
def list_waste(
    branch_id: Optional[int] = Query(None),
    reason: Optional[str] = Query(None, max_length=40),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(WasteRecord).options(
        joinedload(WasteRecord.items).joinedload(WasteItem.inventory_item),
        joinedload(WasteRecord.branch),
        joinedload(WasteRecord.recorded_by_user),
        # Las fotos en una consulta aparte (sin sus bytes: `data` es diferida), no una por merma.
        selectinload(WasteRecord.photos).joinedload(WastePhoto.uploaded_by_user),
    )

    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(WasteRecord.branch_id == efectiva)
    if reason:
        query = query.filter(WasteRecord.reason == reason)
    # Días de Panamá (occurred_at se guarda en UTC sin huso), como en el análisis.
    tz = invu_sales_sync.PANAMA_TZ
    if date_from:
        query = query.filter(WasteRecord.occurred_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None))
    if date_to:
        query = query.filter(WasteRecord.occurred_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None))

    records = query.order_by(WasteRecord.occurred_at.desc(), WasteRecord.id.desc()).offset(offset).limit(limit).all()
    return [_serialize_waste(r) for r in records]

WASTE_ANALYTICS_MAX_DAYS = 366



def _kg_factor(unit: Optional[str]) -> Optional[Decimal]:
    return _KG_POR_UNIDAD.get((unit or "").strip().lower())



def _recorte_kg(record: WasteRecord) -> Optional[Decimal]:
    """Kilos que salieron en esta merma: el peso de balanza o, si no, la cantidad de su único insumo en peso."""
    if record.weight_value is not None:
        factor = _kg_factor(record.weight_unit or "kg")
        return Decimal(record.weight_value) * factor if factor is not None else None
    if len(record.items) == 1:
        factor = _kg_factor(record.items[0].inventory_item.unit)
        if factor is not None:
            return Decimal(record.items[0].quantity) * factor
    return None



def _rendimiento(record: WasteRecord) -> Optional[Decimal]:
    """% aprovechado de lo que se limpió: (limpiado − recorte) / limpiado. Solo en recortes."""
    if record.reason not in PROCESS_WASTE_REASONS or record.processed_value is None:
        return None
    factor = _kg_factor(record.processed_unit or "kg")
    recorte = _recorte_kg(record)
    if factor is None or recorte is None:
        return None
    limpiado = Decimal(record.processed_value) * factor
    if limpiado <= 0 or recorte > limpiado:
        return None
    return ((limpiado - recorte) / limpiado * 100).quantize(Decimal("0.1"))



@router.get("/waste/analytics", response_model=WasteAnalyticsResponse)
def waste_analytics(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    La merma de un período, calculada: cuánto se perdió en plata y en kilos, por día, por
    insumo, por motivo, por sucursal y por tipo (materia prima / de la casa), y cuánto es eso de
    la venta neta de la caja (Invu) en esos mismos días.

    Cómo se calcula, para que los números se puedan defender:
      - Costo de cada línea: el que quedó guardado con la merma (el del último cargamento de ese
        insumo en esa sucursal). Si no hay, el costo de referencia de Invu, y esa parte se
        informa aparte como "estimada". Si tampoco hay, la línea no suma y se cuenta.
      - Kilos: si el insumo se cuenta en una unidad de peso (g, kg, lb...), su cantidad
        convertida. Si va por unidad (la piña), el peso de balanza de la merma cuando esa merma
        tiene un solo insumo; si tiene varios, no se puede repartir y no suma.
      - Días en hora de Panamá, igual que las ventas.
    """
    hasta = date_to or invu_sales_sync.hoy_panama()
    desde = date_from or (hasta - timedelta(days=29))
    if desde > hasta:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La fecha inicial es posterior a la final.")
    if (hasta - desde).days + 1 > WASTE_ANALYTICS_MAX_DAYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El período no puede pasar de un año.")

    efectiva = _visible_branch_filter(current_user, branch_id)
    tz = invu_sales_sync.PANAMA_TZ
    # occurred_at se guarda en UTC sin huso: los bordes del período son medianoche de Panamá.
    inicio_utc = datetime.combine(desde, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    fin_utc = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)

    query = db.query(WasteRecord).options(
        joinedload(WasteRecord.items).joinedload(WasteItem.inventory_item),
        joinedload(WasteRecord.branch),
        selectinload(WasteRecord.photos),
    ).filter(WasteRecord.occurred_at >= inicio_utc, WasteRecord.occurred_at < fin_utc)
    if efectiva is not None:
        query = query.filter(WasteRecord.branch_id == efectiva)
    records = query.all()

    totales = WasteAnalyticsTotals()
    dias = {}
    d = desde
    while d <= hasta:
        dias[d.isoformat()] = WasteAnalyticsDay(date=d.isoformat())
        d += timedelta(days=1)
    por_item: dict = {}
    por_motivo: dict = {}
    por_sucursal: dict = {}
    por_tipo: dict = {}
    por_naturaleza: dict = {}
    rendimientos: dict = {}   # inventory_item_id -> [nombre, kg limpiados, kg de recorte, registros]

    def _sumar(grupos: dict, key: str, label: str, costo: Decimal, kg: Decimal, nuevo_registro: bool):
        g = grupos.setdefault(key, WasteAnalyticsGroup(key=key, label=label))
        g.cost += costo
        g.kg += kg
        if nuevo_registro:
            g.records += 1

    for rec in records:
        totales.records += 1
        if rec.photos:
            totales.records_with_photo += 1
        peso_rec_kg = None
        if rec.weight_value is not None:
            totales.records_with_weight += 1
            factor = _kg_factor(rec.weight_unit or "kg")
            if factor is not None:
                peso_rec_kg = Decimal(rec.weight_value) * factor

        ocurrio = rec.occurred_at if rec.occurred_at.tzinfo else rec.occurred_at.replace(tzinfo=timezone.utc)
        dia = dias.get(ocurrio.astimezone(tz).date().isoformat())
        costo_rec = Decimal("0")
        kg_rec = Decimal("0")
        tipos_vistos = set()

        for line in rec.items:
            item = line.inventory_item
            cantidad = Decimal(line.quantity)
            totales.lines += 1

            estimado = False
            if line.unit_cost is not None:
                costo = cantidad * Decimal(line.unit_cost)
            elif item.effective_cost is not None:
                costo = cantidad * Decimal(item.effective_cost)
                estimado = True
                totales.cost_estimated += costo
            else:
                costo = Decimal("0")
                totales.lines_without_cost += 1

            # Kilos de la línea y si son estimados (una "pieza entera" sin pesar vale el promedio).
            factor = _kg_factor(item.unit)
            por_unidad = _familia_de_unidad(item.unit)[0] == "unidad"
            kg_estimado = False
            if factor is not None:
                kg = cantidad * factor
                kg_estimado = line.mode == "entera" and line.measured_amount is None
            elif por_unidad and line.measured_amount is not None:
                kg = Decimal(line.measured_amount) / 1000         # se pesó: gramos reales
            elif peso_rec_kg is not None and len(rec.items) == 1:
                kg = peso_rec_kg
                kg_estimado = bool(rec.weight_estimated)
            elif por_unidad and item.piece_size:
                kg = cantidad * Decimal(item.piece_size) / 1000   # piezas × gramos de una pieza
                kg_estimado = True
            else:
                kg = None
                totales.lines_without_kg += 1
            if kg is not None and kg_estimado:
                totales.kg_estimated += kg

            costo_rec += costo
            kg_rec += kg or Decimal("0")

            fila = por_item.get(item.id)
            if not fila:
                fila = por_item[item.id] = WasteAnalyticsItem(
                    inventory_item_id=item.id, name=item.name, unit=item.unit, kind=item.kind,
                )
            fila.quantity += cantidad
            fila.cost += costo
            if kg is not None:
                fila.kg = (fila.kg or Decimal("0")) + kg
            fila.estimated = fila.estimated or estimado
            fila.records += 1

            tipo = item.kind or "sin_tipo"
            _sumar(por_tipo, tipo, {KIND_HOUSE: "De la casa", KIND_RAW: "Materia prima"}.get(tipo, "Sin clasificar"),
                   costo, kg or Decimal("0"), tipo not in tipos_vistos)
            tipos_vistos.add(tipo)

        totales.cost_total += costo_rec
        totales.kg_total += kg_rec
        if dia is not None:
            dia.cost += costo_rec
            dia.kg += kg_rec
            dia.records += 1
        _sumar(por_motivo, rec.reason, WASTE_REASON_LABELS.get(rec.reason, rec.reason), costo_rec, kg_rec, True)
        # De proceso (recorte al limpiar: esperado) o evitable (vencido, dañado...: se puede bajar).
        es_proceso = rec.reason in PROCESS_WASTE_REASONS
        if es_proceso:
            totales.cost_process += costo_rec
            totales.kg_process += kg_rec
        _sumar(por_naturaleza, "proceso" if es_proceso else "evitable",
               "De proceso (recorte, limpieza)" if es_proceso else "Evitable (vencido, dañado, error...)",
               costo_rec, kg_rec, True)
        # Rendimiento: solo recortes de UN insumo con lo limpiado anotado (con varios no se puede repartir).
        if es_proceso and rec.processed_value is not None and len(rec.items) == 1:
            factor_proc = _kg_factor(rec.processed_unit or "kg")
            recorte = _recorte_kg(rec)
            if factor_proc is not None and recorte is not None:
                item = rec.items[0].inventory_item
                r = rendimientos.setdefault(item.id, [item.name, Decimal("0"), Decimal("0"), 0])
                r[1] += Decimal(rec.processed_value) * factor_proc
                r[2] += recorte
                r[3] += 1
        _sumar(por_sucursal, str(rec.branch_id), rec.branch.name, costo_rec, kg_rec, True)

    # Venta neta de la caja en el mismo período y sucursales (días ya traídos de Invu).
    ventas_q = db.query(InvuSyncDay.branch_id, func.sum(InvuSyncDay.net_total)).filter(
        InvuSyncDay.business_date >= desde,
        InvuSyncDay.business_date <= hasta,
        InvuSyncDay.net_total.isnot(None),
    )
    if efectiva is not None:
        ventas_q = ventas_q.filter(InvuSyncDay.branch_id == efectiva)
    ventas = {str(b): Decimal(v) for b, v in ventas_q.group_by(InvuSyncDay.branch_id).all() if v is not None}
    if ventas:
        totales.sales_net = sum(ventas.values(), Decimal("0"))

    def _pct(merma: Decimal, venta: Optional[Decimal]) -> Optional[Decimal]:
        if not venta:
            return None
        return (merma / venta * 100).quantize(Decimal("0.01"))

    totales.waste_pct_of_sales = _pct(totales.cost_total, totales.sales_net)
    for key, g in por_sucursal.items():
        g.sales_net = ventas.get(key)
        g.waste_pct_of_sales = _pct(g.cost, g.sales_net)

    def _q(valor: Decimal, lugares: str) -> Decimal:
        return Decimal(valor).quantize(Decimal(lugares))

    totales.cost_total = _q(totales.cost_total, "0.01")
    totales.cost_estimated = _q(totales.cost_estimated, "0.01")
    totales.kg_total = _q(totales.kg_total, "0.001")
    totales.kg_estimated = _q(totales.kg_estimated, "0.001")
    for dia in dias.values():
        dia.cost, dia.kg = _q(dia.cost, "0.01"), _q(dia.kg, "0.001")
    for fila in por_item.values():
        fila.cost = _q(fila.cost, "0.01")
        fila.quantity = _q(fila.quantity, "0.001")
        if fila.kg is not None:
            fila.kg = _q(fila.kg, "0.001")
    totales.cost_process = _q(totales.cost_process, "0.01")
    totales.kg_process = _q(totales.kg_process, "0.001")
    for grupos in (por_motivo, por_sucursal, por_tipo, por_naturaleza):
        for g in grupos.values():
            g.cost, g.kg = _q(g.cost, "0.01"), _q(g.kg, "0.001")

    # Ordenados por plata perdida y, a igual plata (p. ej. sin costos todavía), por kilos.
    orden = lambda x: (x.cost, x.kg or Decimal("0"), x.records)  # noqa: E731
    return WasteAnalyticsResponse(
        date_from=desde.isoformat(),
        date_to=hasta.isoformat(),
        branch_id=efectiva,
        totals=totales,
        by_day=list(dias.values()),
        by_item=sorted(por_item.values(), key=orden, reverse=True),
        by_reason=sorted(por_motivo.values(), key=orden, reverse=True),
        by_branch=sorted(por_sucursal.values(), key=orden, reverse=True),
        by_kind=sorted(por_tipo.values(), key=orden, reverse=True),
        by_nature=sorted(por_naturaleza.values(), key=orden, reverse=True),
        yields=sorted((
            WasteAnalyticsYield(
                inventory_item_id=item_id, name=n, processed_kg=_q(limpio, "0.001"), trimmed_kg=_q(recorte, "0.001"),
                yield_pct=((limpio - recorte) / limpio * 100).quantize(Decimal("0.1")), records=veces,
            )
            for item_id, (n, limpio, recorte, veces) in rendimientos.items() if limpio > 0 and recorte <= limpio
        ), key=lambda y: y.yield_pct),
    )



@router.get("/waste/recipe-usage", response_model=WasteRecipeUsageResponse)
def waste_recipe_usage(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    La merma de cada insumo contra lo que se USÓ de verdad en los platos vendidos, según las
    recetas de Invu. Responde "de todo el arroz que pasó por la cocina, qué parte se botó" y en
    qué platos se usa.

      usado = Σ (platos vendidos × su receta) + Σ (modificadores elegidos × su receta)
      % merma = merma / (usado + merma)

    Y "platos más afectados": la merma de cada insumo se reparte entre los platos que lo usan,
    en proporción a cuánto usa cada uno. Es una estimación, y así se presenta.

    Límites que se informan en vez de esconderse: las preparaciones de la casa (salsas,
    arroces) cuentan como tales, lo que llevan adentro todavía no se desglosa; una receta en una
    unidad que no se puede pasar a la del insumo (unidad contra gramos) no suma.
    """
    hasta = date_to or invu_sales_sync.hoy_panama()
    desde = date_from or (hasta - timedelta(days=29))
    if desde > hasta or (hasta - desde).days + 1 > WASTE_ANALYTICS_MAX_DAYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Período no válido (hasta un año).")
    efectiva = _visible_branch_filter(current_user, branch_id)

    # ---- Lo vendido en el período ----
    platos_q = db.query(
        InvuSaleLine.branch_id, InvuSaleLine.invu_item_id, func.max(InvuSaleLine.name), func.sum(InvuSaleLine.quantity)
    ).filter(
        InvuSaleLine.business_date >= desde, InvuSaleLine.business_date <= hasta,
        InvuSaleLine.counted == True, InvuSaleLine.invu_item_id.isnot(None),  # noqa: E712
    )
    mods_q = db.query(
        InvuSaleLine.branch_id, InvuSaleModifier.invu_modifier_id, func.max(InvuSaleModifier.name), func.sum(InvuSaleModifier.quantity)
    ).join(InvuSaleLine, InvuSaleLine.id == InvuSaleModifier.line_id).filter(
        InvuSaleLine.business_date >= desde, InvuSaleLine.business_date <= hasta,
        InvuSaleLine.counted == True, InvuSaleModifier.invu_modifier_id.isnot(None),  # noqa: E712
    )
    recetas_q = db.query(InvuRecipeLine)
    if efectiva is not None:
        platos_q = platos_q.filter(InvuSaleLine.branch_id == efectiva)
        mods_q = mods_q.filter(InvuSaleLine.branch_id == efectiva)
        recetas_q = recetas_q.filter(InvuRecipeLine.branch_id == efectiva)
    vendidos = [("item",) + tuple(r) for r in platos_q.group_by(InvuSaleLine.branch_id, InvuSaleLine.invu_item_id).all()]
    vendidos += [("modifier",) + tuple(r) for r in mods_q.group_by(InvuSaleLine.branch_id, InvuSaleModifier.invu_modifier_id).all()]

    recetas: dict = {}
    for linea in recetas_q.all():
        recetas.setdefault((linea.branch_id, linea.source_type, linea.source_invu_id), []).append(linea)

    insumos = {i.invu_id: i for i in db.query(InventoryItem).filter(InventoryItem.invu_id.isnot(None))}

    usado: dict = {}                 # inventory_item_id -> Decimal (en su unidad)
    usado_por_plato: dict = {}       # inventory_item_id -> {(tipo, nombre): Decimal}
    unidades_vendidas = Decimal("0")
    unidades_con_receta = Decimal("0")
    lineas_sin_conversion = 0
    for tipo, b_id, source_id, nombre, cantidad in vendidos:
        cantidad = Decimal(cantidad or 0)
        if tipo == "item":
            unidades_vendidas += cantidad
        lineas = recetas.get((b_id, tipo, source_id))
        if not lineas:
            continue
        if tipo == "item":
            unidades_con_receta += cantidad
        for linea in lineas:
            item = insumos.get(linea.product_invu_id)
            if not item:
                continue   # ingrediente archivado en Invu que no se trajo al catálogo
            por_unidad = _a_unidad_del_insumo(Decimal(linea.quantity), linea.unit_name, item.unit, item.piece_size, item.grams_per_ml)
            if por_unidad is None:
                lineas_sin_conversion += 1
                continue
            uso = cantidad * por_unidad
            usado[item.id] = usado.get(item.id, Decimal("0")) + uso
            clave = ("plato" if tipo == "item" else "modificador", nombre or "Sin nombre")
            usado_por_plato.setdefault(item.id, {})
            usado_por_plato[item.id][clave] = usado_por_plato[item.id].get(clave, Decimal("0")) + uso

    # ---- La merma del período (mismo criterio de costo que el análisis) ----
    tz = invu_sales_sync.PANAMA_TZ
    inicio_utc = datetime.combine(desde, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    fin_utc = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    merma_q = db.query(
        WasteItem.inventory_item_id,
        func.sum(WasteItem.quantity),
        func.sum(WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.effective_cost, 0)),
        func.sum(case((WasteItem.unit_cost.is_(None), 1), else_=0)),
    ).join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id).join(
        InventoryItem, InventoryItem.id == WasteItem.inventory_item_id
    ).filter(WasteRecord.occurred_at >= inicio_utc, WasteRecord.occurred_at < fin_utc)
    if efectiva is not None:
        merma_q = merma_q.filter(WasteRecord.branch_id == efectiva)
    merma = {r[0]: (Decimal(r[1] or 0), Decimal(r[2] or 0), bool(r[3])) for r in merma_q.group_by(WasteItem.inventory_item_id).all()}

    items_por_id = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.id.in_(list(merma.keys()) or [0]))}
    filas: List[WasteRecipeUsageItem] = []
    platos_afectados: dict = {}
    for item_id, (cant, costo, estimado) in merma.items():
        item = items_por_id.get(item_id)
        if not item:
            continue
        uso = usado.get(item_id)
        por_plato = usado_por_plato.get(item_id, {})
        top = sorted(por_plato.items(), key=lambda kv: kv[1], reverse=True)
        filas.append(WasteRecipeUsageItem(
            inventory_item_id=item.id, name=item.name, unit=item.unit, kind=item.kind,
            wasted=cant.quantize(Decimal("0.001")), wasted_cost=costo.quantize(Decimal("0.01")), estimated=estimado,
            used=(uso.quantize(Decimal("0.001")) if uso else None),
            waste_pct=((cant / (uso + cant) * 100).quantize(Decimal("0.1")) if uso else None),
            dishes=[WasteRecipeDishShare(name=n, type=t, used=u.quantize(Decimal("0.001")),
                                          share_pct=(u / uso * 100).quantize(Decimal("0.1")))
                    for (t, n), u in top[:3]] if uso else [],
        ))
        if uso and costo:
            for (t, n), u in por_plato.items():
                p = platos_afectados.setdefault((t, n), {"cost": Decimal("0"), "items": {}})
                parte = costo * u / uso
                p["cost"] += parte
                p["items"][item.name] = p["items"].get(item.name, Decimal("0")) + parte

    filas.sort(key=lambda f: (f.waste_pct is not None, f.waste_pct or 0, f.wasted_cost), reverse=True)
    platos = sorted(platos_afectados.items(), key=lambda kv: kv[1]["cost"], reverse=True)[:10]
    return WasteRecipeUsageResponse(
        date_from=desde.isoformat(), date_to=hasta.isoformat(), branch_id=efectiva,
        recipes_synced_at=invu_recipes_sync.ultima_sincronizacion(db),
        recipes_count=len(recetas),
        recipes_running=bool(invu_recipes_sync.estado().get("running")),
        sold_units=unidades_vendidas.quantize(Decimal("1")),
        sold_units_with_recipe=unidades_con_receta.quantize(Decimal("1")),
        lines_without_conversion=lineas_sin_conversion,
        items=filas,
        dishes=[WasteRecipeDish(name=n, type=t, allocated_cost=v["cost"].quantize(Decimal("0.01")),
                                ingredients=[k for k, _ in sorted(v["items"].items(), key=lambda kv: kv[1], reverse=True)[:3]])
                for (t, n), v in platos],
    )



@router.post("/invu/sync-recipes", status_code=status.HTTP_202_ACCEPTED)
def sync_recipes_from_invu(current_user: User = Depends(require_permission("integrations.manage"))):
    """
    Trae las recetas de Invu ahora (en segundo plano: son cientos de llamadas, ~12 min por sucursal).
    La pantalla consulta el avance en GET /waste/recipe-usage (`recipes_running`).
    """
    if not invu_client.is_configured() or not settings.invu_branch_credentials():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="La integración con Invu no está configurada en el servidor.")
    iniciada = invu_recipes_sync.lanzar_en_segundo_plano()
    logger.info(f"Sincronización de recetas pedida por {current_user.name} ({'iniciada' if iniciada else 'ya estaba corriendo'})")
    return {"started": iniciada, "running": True}



# ---- Evidencia de la merma: fotos de lo que se descartó ----
# El navegador las achica a ~300 KB antes de subirlas; el tope es para una que llegue entera.
WASTE_PHOTO_MAX_BYTES = 8 * 1024 * 1024

WASTE_PHOTOS_PER_RECORD = 6



def _image_type(data: bytes) -> Optional[str]:
    """El tipo real por los primeros bytes, no por lo que dice el navegador ni la extensión."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None



def _waste_for_user(db: Session, waste_id: int, current_user: User) -> WasteRecord:
    """La merma, si quien pregunta puede verla (misma regla de sucursal que el listado)."""
    record = db.query(WasteRecord).filter(WasteRecord.id == waste_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merma no encontrada.")
    efectiva = _visible_branch_filter(current_user, record.branch_id)
    if efectiva is not None and efectiva != record.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a las mermas de otra sucursal.")
    return record



@router.post("/waste/{waste_id}/photos", response_model=WasteResponse, status_code=status.HTTP_201_CREATED)
async def add_waste_photo(
    waste_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Agrega una foto a una merma ya registrada (se llama una vez por foto, justo después de
    crearla o más tarde desde su detalle). Solo JPG, PNG o WebP, verificado por su contenido.
    """
    record = _waste_for_user(db, waste_id, current_user)
    if len(record.photos) >= WASTE_PHOTOS_PER_RECORD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Una merma admite hasta {WASTE_PHOTOS_PER_RECORD} fotos.",
        )

    data = await file.read(WASTE_PHOTO_MAX_BYTES + 1)
    if len(data) > WASTE_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="La foto supera los 8 MB.")
    content_type = _image_type(data)
    if not content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se aceptan fotos (JPG, PNG o WebP).",
        )

    record.photos.append(WastePhoto(
        content_type=content_type,
        size_bytes=len(data),
        data=data,
        uploaded_by_user_id=current_user.id,
    ))
    log_audit_event(
        db, current_user.id, record.branch_id, "waste.photo_add", "waste_record", record.id,
        {"size_bytes": len(data), "content_type": content_type}
    )
    db.commit()
    db.refresh(record)
    return _serialize_waste(record)



@router.get("/waste/{waste_id}/photos/{photo_id}")
def get_waste_photo(
    waste_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    record = _waste_for_user(db, waste_id, current_user)
    photo = db.query(WastePhoto).filter(WastePhoto.id == photo_id, WastePhoto.waste_record_id == record.id).first()
    if not photo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto no encontrada.")
    return Response(
        content=photo.data,
        media_type=photo.content_type,
        headers={
            # Una foto de merma no cambia nunca: se puede guardar en el navegador de quien la vio.
            "Cache-Control": "private, max-age=86400",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )



def _chequear_fecha_posterior_a_conteo(db: Session, branch_id: int, item_ids: List[int], ocurrio: datetime) -> None:
    """
    Un registro con fecha anterior al último conteo de ese insumo ya está dentro de lo que se
    contó: sumarlo ahora le correría la existencia a lo que había en el estante. Se rechaza; se
    anota con la hora actual o se corrige con un conteo nuevo.
    """
    ocurrio_utc = ocurrio.astimezone(timezone.utc).replace(tzinfo=None) if ocurrio.tzinfo else ocurrio
    contados = db.query(InventoryItem.name).select_from(StockCountItem).join(
        StockCount, StockCount.id == StockCountItem.stock_count_id
    ).join(InventoryItem, InventoryItem.id == StockCountItem.inventory_item_id).filter(
        StockCount.branch_id == branch_id, StockCount.counted_at > ocurrio_utc,
        StockCountItem.inventory_item_id.in_(item_ids or [0]),
    ).distinct().all()
    if contados:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"No se puede anotar con esa fecha: {', '.join(sorted(n for (n,) in contados))} se contó después "
                    "y el conteo ya dejó la existencia en lo que había. Anótalo con la hora actual o corrige con un conteo."),
        )



@router.delete("/waste/{waste_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_waste(
    waste_id: int,
    motivo: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Borra una merma cargada por error, con sus líneas, sus fotos y sus movimientos del libro, así
    la existencia y el análisis vuelven a quedar como si nunca se hubiera cargado.

    - Supervisor y admin (permiso `inventory.adjust`): cualquier merma de las sucursales que ven.
    - Quien la registró: la suya, dentro de las primeras 24 horas.

    No queda un hueco sin rastro: la auditoría guarda una copia de lo que se borró (motivo,
    insumos, cantidades, costo, quién la había cargado) y el motivo del borrado si se dio.
    """
    record = _waste_for_user(db, waste_id, current_user)
    _chequear_quien_borra(current_user, record.recorded_by_user_id, record.created_at, "la merma")
    _chequear_sin_conteo_posterior(db, record.branch_id, [l.inventory_item_id for l in record.items],
                                   record.created_at, "esta merma")

    snapshot = {
        "reason": record.reason,
        "occurred_at": record.occurred_at.isoformat() if record.occurred_at else None,
        "recorded_by_user_id": record.recorded_by_user_id,
        "notes": record.notes,
        "weight_value": str(record.weight_value) if record.weight_value is not None else None,
        "photos": len(record.photos),
        "items": [
            {
                "inventory_item_id": l.inventory_item_id,
                "quantity": str(l.quantity),
                "unit_cost": str(l.unit_cost) if l.unit_cost is not None else None,
            }
            for l in record.items
        ],
        "delete_reason": (motivo or "").strip() or None,
    }

    db.query(InventoryMovement).filter(
        InventoryMovement.source_type == "waste",
        InventoryMovement.source_id == record.id,
    ).delete(synchronize_session=False)
    log_audit_event(db, current_user.id, record.branch_id, "waste.delete", "waste_record", record.id, snapshot)
    db.delete(record)  # líneas y fotos se van con ella (cascade)
    db.commit()
    logger.info(f"Merma #{waste_id} borrada por {current_user.name}" + (f" — {snapshot['delete_reason']}" if snapshot["delete_reason"] else ""))
    return None



@router.get("/waste/{waste_id:int}", response_model=WasteResponse)
def get_waste(
    waste_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Una merma (si quien pregunta ve esa sucursal): la abre el aviso de "merma importante"."""
    return _serialize_waste(_waste_for_user(db, waste_id, current_user))
