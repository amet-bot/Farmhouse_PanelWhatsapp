import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem, KIND_HOUSE, KIND_RAW
from models.invu_sales import InvuRecipeLine, InvuSaleLine, InvuSaleModifier, InvuSyncDay
from models.supplier import Supplier
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem, WastePhoto
from models.stock_count import StockCount, StockCountItem
from models.inventory_movement import InventoryMovement
from models.transfer import Transfer, TransferItem
from schemas.inventory import (
    InventoryItemCreate, InventoryItemPieceSize, InventoryItemResponse,
    InvuStatusResponse, InvuSyncResult,
    SupplierCreate, SupplierResponse,
    ShipmentCreate, ShipmentResponse, ShipmentItemResponse,
    StockCountCreate, StockCountItemResponse, StockCountResponse,
    StockRowResponse, WasteCreate, WasteItemResponse, WastePhotoResponse, WasteReasonResponse, WasteResponse,
    WasteAnalyticsGroup, WasteAnalyticsItem, WasteAnalyticsDay, WasteAnalyticsResponse, WasteAnalyticsTotals, WasteAnalyticsYield,
    WasteRecipeDish, WasteRecipeDishShare, WasteRecipeUsageItem, WasteRecipeUsageResponse,
    MovementComparisonResponse,
)
from config import settings
from services import invu_client, invu_items_sync, invu_recipes_sync, invu_sales_sync, invu_sync
from services.audit import log_audit_event
from security.auth import get_current_authorized_user
from security.permissions import has_permission, require_permission
from security.access_control import check_target_branch_valid

logger = logging.getLogger("farmhouse.inventory")

router = APIRouter(prefix="/inventory", tags=["Inventario"])

# Vocabulario de la merma. Lista cerrada y no texto libre porque el sentido del módulo es poder
# decir "este mes se perdió tanto por vencimiento": con motivos escritos a mano cada sucursal
# inventa el suyo y no suma nada. "Otro" existe para lo que no entra, y la nota recoge el detalle.
# El orden es el que se ve en el formulario: primero lo que más pasa.
WASTE_REASONS = (
    # Primero porque es lo más común en cocina: lo que se saca al limpiar (piel y grasa del pollo,
    # cáscaras). Es merma de PROCESO, esperada; el resto de la lista es merma evitable, y el
    # análisis las separa (ver PROCESS_WASTE_REASONS).
    ("recorte", "Recorte o limpieza"),
    ("vencido", "Vencido"),
    ("danado", "Dañado o golpeado"),
    ("error_preparacion", "Error de preparación"),
    ("derrame", "Derrame o rotura"),
    ("devolucion", "Devolución de cliente"),
    ("consumo_interno", "Consumo interno"),
    ("faltante", "Faltante o robo"),
    ("otro", "Otro"),
)
WASTE_REASON_LABELS = dict(WASTE_REASONS)
# Merma de proceso: parte de preparar el insumo, se mide contra lo que se limpió (rendimiento).
PROCESS_WASTE_REASONS = {"recorte"}


def _serialize_shipment(shipment: Shipment) -> ShipmentResponse:
    items: List[ShipmentItemResponse] = []
    total_cost = Decimal("0.00")
    has_cost = False
    for line in shipment.items:
        if line.unit_cost is not None:
            total_cost += (Decimal(line.quantity) * Decimal(line.unit_cost))
            has_cost = True
        items.append(ShipmentItemResponse(
            id=line.id,
            inventory_item_id=line.inventory_item_id,
            item_name=line.inventory_item.name,
            unit=line.inventory_item.unit,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
        ))
    return ShipmentResponse(
        id=shipment.id,
        branch_id=shipment.branch_id,
        branch_name=shipment.branch.name,
        received_by_user_id=shipment.received_by_user_id,
        received_by_name=shipment.received_by_user.name,
        received_at=shipment.received_at,
        supplier_id=shipment.supplier_id,
        supplier_name=(shipment.supplier.name if shipment.supplier else None),
        notes=shipment.notes,
        created_at=shipment.created_at,
        items=items,
        total_cost=total_cost.quantize(Decimal("0.01")) if has_cost else None,
    )


@router.get("/items", response_model=List[InventoryItemResponse])
def search_inventory_items(
    q: str = Query("", max_length=150),
    # 500: con los ingredientes de Invu el catálogo ronda los 160-200 y la pantalla lo pide entero.
    limit: int = Query(8, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Autocomplete del catálogo (insumos y productos mezclados, ver InventoryItem).
    `limit` por defecto 8 para el autocomplete; la pantalla de catálogo de /inventario pide
    más de una vez para listar el catálogo entero.
    """
    query = db.query(InventoryItem).filter(InventoryItem.active == True)
    q = q.strip()
    if q:
        query = query.filter(InventoryItem.name.ilike(f"%{q}%"))
    return query.order_by(InventoryItem.name.asc()).limit(limit).all()


@router.post("/items", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED)
def create_inventory_item(
    item_in: InventoryItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Cualquier usuario logueado puede crear un ítem nuevo (no solo admin): son los encargados de
    sucursal quienes lo van a necesitar sobre la marcha al registrar un cargamento.
    """
    name = item_in.name.strip()
    existing = db.query(InventoryItem).filter(InventoryItem.name.ilike(name)).first()
    if existing:
        return existing

    item = InventoryItem(name=name, unit=item_in.unit.strip(), category=(item_in.category or None))
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # Carrera: dos sucursales crearon el mismo ítem casi al mismo tiempo.
        db.rollback()
        existing = db.query(InventoryItem).filter(InventoryItem.name.ilike(name)).first()
        if existing:
            return existing
        raise
    db.refresh(item)
    return item


@router.get("/suppliers", response_model=List[SupplierResponse])
def search_suppliers(
    q: str = Query("", max_length=150),
    limit: int = Query(8, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Autocomplete del catálogo de proveedores (calcado de search_inventory_items, `limit` incluido)."""
    query = db.query(Supplier).filter(Supplier.active == True)
    q = q.strip()
    if q:
        query = query.filter(Supplier.name.ilike(f"%{q}%"))
    return query.order_by(Supplier.name.asc()).limit(limit).all()


@router.post("/suppliers", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    supplier_in: SupplierCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Crea un proveedor en el panel. Calcado de create_inventory_item, con una diferencia:

    **Si la integración con Invu está configurada, esto se rechaza.** Los proveedores se dan de
    alta en Invu, que es donde tienen RUC y contacto; dejar crearlos también acá produciría uno
    que en Invu no existe y que la próxima sincronización no sabría emparejar. Cuando NO hay
    credenciales el camino sigue abierto, porque si no el sistema se quedaría sin ninguna forma
    de cargar un proveedor hasta que alguien configure la integración.
    """
    if invu_client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Los proveedores se dan de alta en Invu. Cargalo allá y sincronizá desde Proveedores.",
        )

    name = supplier_in.name.strip()
    existing = db.query(Supplier).filter(Supplier.name.ilike(name)).first()
    if existing:
        return existing

    supplier = Supplier(name=name, phone=(supplier_in.phone or None))
    db.add(supplier)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Supplier).filter(Supplier.name.ilike(name)).first()
        if existing:
            return existing
        raise
    db.refresh(supplier)
    return supplier


@router.post("/shipments", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(
    shipment_in: ShipmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    # Igual que conversations.py: agente y supervisor local solo su propia sucursal (Punto 3).
    if current_user.role == "agent":
        if shipment_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar cargamentos en otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if shipment_in.branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para registrar cargamentos en otra sucursal."
            )

    check_target_branch_valid(db, shipment_in.branch_id)

    if shipment_in.supplier_id is not None:
        supplier = db.query(Supplier).filter(Supplier.id == shipment_in.supplier_id).first()
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El proveedor seleccionado no existe."
            )

    item_ids = [line.inventory_item_id for line in shipment_in.items]
    found_items = db.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).all()
    missing = set(item_ids) - {i.id for i in found_items}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ítem(s) de inventario no encontrados: {sorted(missing)}"
        )

    shipment = Shipment(
        branch_id=shipment_in.branch_id,
        received_by_user_id=current_user.id,
        received_at=shipment_in.received_at or datetime.now(timezone.utc),
        supplier_id=shipment_in.supplier_id,
        notes=(shipment_in.notes or None),
    )
    for line in shipment_in.items:
        shipment.items.append(ShipmentItem(
            inventory_item_id=line.inventory_item_id,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
        ))

    db.add(shipment)
    db.flush()  # asigna shipment.id antes de generar los movimientos del libro (Fase 4)
    for line in shipment.items:
        db.add(InventoryMovement(
            branch_id=shipment.branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="in",
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            occurred_at=shipment.received_at,
            source_type="shipment",
            source_id=shipment.id,
            created_by_user_id=current_user.id,
        ))
    log_audit_event(
        db, current_user.id, shipment.branch_id, "shipment.create", "shipment", shipment.id,
        {"items": len(shipment.items), "supplier_id": shipment.supplier_id}
    )
    db.commit()
    db.refresh(shipment)
    logger.info(f"Cargamento #{shipment.id} registrado en sucursal {shipment.branch_id} por {current_user.name}")
    return _serialize_shipment(shipment)


@router.get("/shipments", response_model=List[ShipmentResponse])
def list_shipments(
    branch_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    query = db.query(Shipment)

    if current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None):
        if branch_id is not None:
            query = query.filter(Shipment.branch_id == branch_id)
    else:
        query = query.filter(Shipment.branch_id == current_user.branch_id)

    shipments = query.order_by(Shipment.received_at.desc()).offset(offset).limit(limit).all()
    return [_serialize_shipment(s) for s in shipments]


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


def _on_hand_map(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """Existencia actual (entradas - mermas + diferencias de conteo ± traslados) de esos insumos en esa sucursal."""
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
    return {
        item_id: (
            Decimal(entradas.get(item_id, 0)) - Decimal(salidas.get(item_id, 0))
            + Decimal(ajustes.get(item_id, 0)) + traslados.get(item_id, Decimal("0"))
        )
        for item_id in item_ids
    }


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
        elif line.inventory_item.reference_cost is not None:
            display += Decimal(line.quantity) * Decimal(line.inventory_item.reference_cost)
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
            reference_cost=(line.inventory_item.reference_cost if line.unit_cost is None else None),
            mode=line.mode,
            pieces=line.pieces,
            piece_size=line.inventory_item.piece_size,
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


def _familia_de_unidad(unit: Optional[str]) -> tuple:
    """
    ("peso", gramos por unidad) | ("volumen", ml por unidad) | ("unidad", 1).
    Lo que no es peso ni volumen ("unidad", "caja", "bolsa"...) se cuenta por pieza.
    """
    fam = _UNIT_FAMILY.get((unit or "").strip().lower())
    if fam and fam[0] in ("peso", "volumen"):
        return fam[0], fam[1] * 1000
    return "unidad", Decimal("1")


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
        else:
            if not piece_size:
                falta(f"falta {medida} una pieza entera.")
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


@router.post("/waste", response_model=WasteResponse, status_code=status.HTTP_201_CREATED)
def create_waste(
    waste_in: WasteCreate,
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
        if line.piece_size is not None and (tamano is None or puede_ajustar):
            if tamano != line.piece_size:
                log_audit_event(db, current_user.id, waste_in.branch_id, "item.piece_size", "inventory_item", item.id,
                                {"before": str(tamano) if tamano is not None else None,
                                 "after": str(line.piece_size), "via": "waste"})
            item.piece_size = tamano = line.piece_size
        cantidades.append(_cantidad_de_linea(item, line, tamano))

    # La existencia se mira ANTES de grabar: después este mismo registro ya estaría restando.
    stock_before = _on_hand_map(db, waste_in.branch_id, item_ids)

    record = WasteRecord(
        branch_id=waste_in.branch_id,
        recorded_by_user_id=current_user.id,
        occurred_at=waste_in.occurred_at or datetime.now(timezone.utc),
        reason=waste_in.reason,
        notes=(waste_in.notes or None),
        weight_value=waste_in.weight_value,
        weight_unit=((waste_in.weight_unit or "kg") if waste_in.weight_value is not None else None),
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
    logger.info(
        f"Merma #{record.id} ({record.reason}) en sucursal {record.branch_id} por {current_user.name}"
        + (f" — deja en negativo: {', '.join(respuesta.negative_items)}" if respuesta.negative_items else "")
    )
    return respuesta


@router.get("/waste", response_model=List[WasteResponse])
def list_waste(
    branch_id: Optional[int] = Query(None),
    reason: Optional[str] = Query(None, max_length=40),
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

    records = query.order_by(WasteRecord.occurred_at.desc(), WasteRecord.id.desc()).offset(offset).limit(limit).all()
    return [_serialize_waste(r) for r in records]


# ---- Análisis de merma ----
# Cuántos kg es una unidad de cada nombre de unidad que usan los insumos (los de Invu vienen como
# "gramos" / "kilogramo"; los cargados a mano, como "kg"). Lo que no está acá no es un peso.
_KG_POR_UNIDAD = {
    "kg": Decimal("1"), "kilo": Decimal("1"), "kilos": Decimal("1"), "kilogramo": Decimal("1"), "kilogramos": Decimal("1"),
    "g": Decimal("0.001"), "gr": Decimal("0.001"), "gramo": Decimal("0.001"), "gramos": Decimal("0.001"),
    "lb": Decimal("0.45359237"), "libra": Decimal("0.45359237"), "libras": Decimal("0.45359237"),
    "oz": Decimal("0.028349523"), "onza": Decimal("0.028349523"), "onzas": Decimal("0.028349523"),
}
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
            elif item.reference_cost is not None:
                costo = cantidad * Decimal(item.reference_cost)
                estimado = True
                totales.cost_estimated += costo
            else:
                costo = Decimal("0")
                totales.lines_without_cost += 1

            factor = _kg_factor(item.unit)
            if factor is not None:
                kg = cantidad * factor
            elif peso_rec_kg is not None and len(rec.items) == 1:
                kg = peso_rec_kg
            elif item.piece_size and _familia_de_unidad(item.unit)[0] == "unidad":
                kg = cantidad * Decimal(item.piece_size) / 1000   # piezas × gramos de una pieza
            else:
                kg = None
                totales.lines_without_kg += 1

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


# ---- Merma × recetas de Invu ----
# Para pasar la cantidad de una receta a la unidad del insumo: (familia, factor a la base de la
# familia). Solo se convierte dentro de la misma familia (peso con peso, volumen con volumen).
_UNIT_FAMILY = {
    **{k: ("peso", v) for k, v in _KG_POR_UNIDAD.items()},
    "ml": ("volumen", Decimal("0.001")), "mililitro": ("volumen", Decimal("0.001")), "mililitros": ("volumen", Decimal("0.001")),
    "l": ("volumen", Decimal("1")), "litro": ("volumen", Decimal("1")), "litros": ("volumen", Decimal("1")),
    "unidad": ("unidad", Decimal("1")), "unidades": ("unidad", Decimal("1")), "u": ("unidad", Decimal("1")), "und": ("unidad", Decimal("1")),
}


def _a_unidad_del_insumo(cantidad: Decimal, unidad_receta: Optional[str], unidad_insumo: Optional[str]) -> Optional[Decimal]:
    """La cantidad de la receta en la unidad del insumo, o None si no se puede convertir."""
    r = (unidad_receta or "").strip().lower()
    i = (unidad_insumo or "").strip().lower()
    if not r or r == i:
        return cantidad
    fr, fi = _UNIT_FAMILY.get(r), _UNIT_FAMILY.get(i)
    if fr and fi and fr[0] == fi[0]:
        return cantidad * fr[1] / fi[1]
    return None


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
            por_unidad = _a_unidad_del_insumo(Decimal(linea.quantity), linea.unit_name, item.unit)
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
        func.sum(WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.reference_cost, 0)),
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


# Quien registró una merma puede borrarla solo, sin pedírselo a nadie, mientras sea un error
# reciente (se equivocó de insumo, de cantidad o la cargó dos veces). Pasado ese plazo la merma ya
# entró en los reportes y borrarla es corregir historia: eso queda para supervisor o admin.
WASTE_SELF_DELETE_WINDOW = timedelta(hours=24)


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

    if not has_permission(current_user, "inventory.adjust"):
        if record.recorded_by_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo quien registró la merma, o un supervisor, puede borrarla.",
            )
        cargada = record.created_at
        if cargada.tzinfo is None:
            cargada = cargada.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - cargada > WASTE_SELF_DELETE_WINDOW:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pasaron más de 24 horas desde que se cargó: pedile a un supervisor que la borre.",
            )

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


# ==========================================================================
# Existencias
# ==========================================================================
@router.get("/stock", response_model=List[StockRowResponse])
def list_stock(
    branch_id: Optional[int] = Query(None),
    q: str = Query("", max_length=150),
    only_stocked: bool = Query(False, description="Deja fuera los insumos que nunca se movieron"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Existencias por insumo: entró, salió por merma, se corrigió por conteo, y lo que queda.

    Se calcula con tres sumas agrupadas cada vez que se pide, sin tabla de saldos. Con el tamaño
    de este negocio son dos consultas sobre miles de renglones, no millones; el día que eso deje
    de alcanzar, el arreglo es una tabla de saldos por sucursal, no parchar el cálculo.

    `branch_id` ausente en un usuario global suma TODAS las sucursales en una fila por insumo:
    lo que sirve para comprar es el total de la casa, no cuatro listas separadas.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)

    entradas_q = (
        db.query(
            ShipmentItem.inventory_item_id.label("item_id"),
            func.coalesce(func.sum(ShipmentItem.quantity), 0).label("cantidad"),
            func.max(Shipment.received_at).label("ultimo"),
        )
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
    )
    # Pérdida en $: el costo guardado con la merma y, si no hay, el de referencia de Invu (mismo
    # criterio que el Análisis de merma; `costo_estimado` dice cuánto salió de Invu). Antes solo
    # contaba el guardado y, sin cargamentos registrados, la columna quedaba toda en "—".
    salidas_q = (
        db.query(
            WasteItem.inventory_item_id.label("item_id"),
            func.coalesce(func.sum(WasteItem.quantity), 0).label("cantidad"),
            func.coalesce(func.sum(
                WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.reference_cost, 0)
            ), 0).label("costo"),
            func.coalesce(func.sum(
                case((WasteItem.unit_cost.is_(None), WasteItem.quantity * func.coalesce(InventoryItem.reference_cost, 0)), else_=0)
            ), 0).label("costo_estimado"),
            func.max(WasteRecord.occurred_at).label("ultimo"),
        )
        .join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id)
        .join(InventoryItem, InventoryItem.id == WasteItem.inventory_item_id)
    )
    ajustes_q = (
        db.query(
            StockCountItem.inventory_item_id.label("item_id"),
            func.coalesce(func.sum(StockCountItem.difference), 0).label("cantidad"),
            func.max(StockCount.counted_at).label("ultimo"),
        )
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
    )
    if efectiva is not None:
        entradas_q = entradas_q.filter(Shipment.branch_id == efectiva)
        salidas_q = salidas_q.filter(WasteRecord.branch_id == efectiva)
        ajustes_q = ajustes_q.filter(StockCount.branch_id == efectiva)

    entradas = {r.item_id: r for r in entradas_q.group_by(ShipmentItem.inventory_item_id).all()}
    salidas = {r.item_id: r for r in salidas_q.group_by(WasteItem.inventory_item_id).all()}
    ajustes = {r.item_id: r for r in ajustes_q.group_by(StockCountItem.inventory_item_id).all()}
    traslados = _transfer_net_map(db, efectiva)

    ultimos_costos = _last_costs_map(db, efectiva) if efectiva is not None else {}

    catalogo_q = db.query(InventoryItem).filter(InventoryItem.active == True)
    termino = q.strip()
    if termino:
        catalogo_q = catalogo_q.filter(InventoryItem.name.ilike(f"%{termino}%"))

    branch_name = None
    if efectiva is not None:
        branch = db.query(Branch).filter(Branch.id == efectiva).first()
        branch_name = branch.name if branch else None

    filas: List[StockRowResponse] = []
    for item in catalogo_q.order_by(InventoryItem.name.asc()).all():
        entrada = entradas.get(item.id)
        salida = salidas.get(item.id)
        ajuste = ajustes.get(item.id)
        # Un insumo que solo se contó también "se movió": el conteo de arranque es justamente
        # su primer movimiento.
        trasladado = traslados.get(item.id, Decimal("0"))
        if only_stocked and not entrada and not salida and not ajuste and item.id not in traslados:
            continue

        entro = Decimal(entrada.cantidad) if entrada else Decimal("0")
        salio = Decimal(salida.cantidad) if salida else Decimal("0")
        ajustado = Decimal(ajuste.cantidad) if ajuste else Decimal("0")
        fechas = [f for f in (
            (entrada.ultimo if entrada else None),
            (salida.ultimo if salida else None),
            (ajuste.ultimo if ajuste else None),
        ) if f]

        filas.append(StockRowResponse(
            inventory_item_id=item.id,
            item_name=item.name,
            unit=item.unit,
            category=item.category,
            branch_id=efectiva,
            branch_name=branch_name,
            entered=entro,
            wasted=salio,
            adjusted=ajustado,
            transferred=trasladado,
            on_hand=entro - salio + ajustado + trasladado,
            wasted_cost=(Decimal(salida.costo).quantize(Decimal("0.01")) if salida and salida.costo else None),
            wasted_cost_estimated=bool(salida and salida.costo_estimado and Decimal(salida.costo_estimado) > 0),
            last_movement_at=(max(fechas) if fechas else None),
            last_unit_cost=ultimos_costos.get(item.id),
            last_counted_at=(ajuste.ultimo if ajuste else None),
        ))

    return filas


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
    logger.info(
        f"Conteo #{record.id} en sucursal {record.branch_id} por {current_user.name}: "
        f"{len(record.items)} insumos, {respuesta.mismatched_count} con diferencia"
        + (" (arranque)" if es_primero else "")
    )
    return respuesta


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


# ==========================================================================
# Libro de movimientos (Fase 4) — solo lectura, en observación
# ==========================================================================
@router.get("/movements/compare", response_model=List[MovementComparisonResponse])
def compare_movements_with_formula(
    branch_id: int = Query(...),
    only_mismatches: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Compara, insumo por insumo, la existencia de la fórmula de siempre contra la que da el libro
    de movimientos nuevo. Solo lectura: no cambia cuál manda, existe para poder observar si
    coinciden antes de decidir eso.
    """
    if current_user.role == "agent":
        if branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para ver el inventario de otra sucursal."
            )
    elif current_user.role == "supervisor" and current_user.branch_id:
        if branch_id != current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para ver el inventario de otra sucursal."
            )

    check_target_branch_valid(db, branch_id)

    items = db.query(InventoryItem).order_by(InventoryItem.name).all()
    item_ids = [i.id for i in items]

    formula = _on_hand_map(db, branch_id, item_ids)
    desde_movimientos = dict(
        db.query(InventoryMovement.inventory_item_id, func.coalesce(func.sum(InventoryMovement.quantity), 0))
        .filter(InventoryMovement.branch_id == branch_id, InventoryMovement.inventory_item_id.in_(item_ids))
        .group_by(InventoryMovement.inventory_item_id)
        .all()
    )

    filas = []
    for item in items:
        f = formula.get(item.id, Decimal("0"))
        m = Decimal(desde_movimientos.get(item.id, 0))
        coincide = f == m
        if only_mismatches and coincide:
            continue
        filas.append(MovementComparisonResponse(
            inventory_item_id=item.id,
            item_name=item.name,
            on_hand_formula=f,
            on_hand_movements=m,
            matches=coincide,
        ))
    return filas


# ==========================================================================
# Invu POS: de dónde vienen los proveedores
# ==========================================================================
@router.get("/invu/status", response_model=InvuStatusResponse)
def invu_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Si la integración manda o no, y desde cuándo. La pantalla lo necesita para saber si mostrar
    "Nuevo proveedor" o "Sincronizar con Invu": son excluyentes.
    """
    configurada = invu_client.is_configured()
    return InvuStatusResponse(
        configured=configurada,
        last_synced_at=(invu_sync.ultima_sincronizacion(db) if configurada else None),
        synced_count=db.query(func.count(Supplier.id)).filter(
            Supplier.invu_id.isnot(None), Supplier.active == True
        ).scalar() or 0,
        inactive_count=db.query(func.count(Supplier.id)).filter(
            Supplier.invu_id.isnot(None), Supplier.active == False
        ).scalar() or 0,
        local_count=db.query(func.count(Supplier.id)).filter(
            Supplier.invu_id.is_(None), Supplier.active == True
        ).scalar() or 0,
        items_last_synced_at=(invu_items_sync.ultima_sincronizacion(db) if configurada else None),
        items_synced_count=db.query(func.count(InventoryItem.id)).filter(
            InventoryItem.invu_id.isnot(None), InventoryItem.active == True
        ).scalar() or 0,
        items_local_count=db.query(func.count(InventoryItem.id)).filter(
            InventoryItem.invu_id.is_(None), InventoryItem.active == True
        ).scalar() or 0,
    )


@router.post("/invu/sync-items", response_model=InvuSyncResult)
def sync_items_from_invu(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Trae los insumos (Ingredientes de Invu) ahora mismo. Pasada COMPLETA, como la de
    proveedores: quien aprieta el botón sospecha que falta algo.
    """
    if not invu_client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La integración con Invu no está configurada en el servidor.",
        )

    try:
        resumen = invu_items_sync.sync_items(db)
    except invu_client.InvuError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    logger.info(f"Sincronización de insumos pedida por {current_user.name}: {resumen}")
    return InvuSyncResult(
        received=resumen["recibidos"],
        created=resumen["creados"],
        linked=resumen["enlazados"],
        updated=resumen["actualizados"],
        deactivated=resumen["apagados"],
        synced_at=resumen["sincronizado_en"],
    )


@router.post("/invu/sync-suppliers", response_model=InvuSyncResult)
def sync_suppliers_from_invu(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Trae los proveedores de Invu ahora mismo.

    Pasada COMPLETA, no incremental: el sweep diario ya hace la incremental, y quien aprieta
    este botón normalmente es alguien que sospecha que algo no está: darle solo "lo que cambió
    desde la última vez" sería contestarle con la misma foto que no le sirvió.
    """
    if not invu_client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La integración con Invu no está configurada en el servidor.",
        )

    try:
        resumen = invu_sync.sync_providers(db)
    except invu_client.InvuError as e:
        # Un problema hablando con Invu no es un error del panel: se cuenta tal cual, con el
        # mensaje que sirve para ir a arreglarlo.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    logger.info(f"Sincronización de proveedores pedida por {current_user.name}: {resumen}")
    return InvuSyncResult(
        received=resumen["recibidos"],
        created=resumen["creados"],
        linked=resumen["enlazados"],
        updated=resumen["actualizados"],
        synced_at=resumen["sincronizado_en"],
    )
