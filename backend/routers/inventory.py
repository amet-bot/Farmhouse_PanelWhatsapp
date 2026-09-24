import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.supplier import Supplier
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem
from models.stock_count import StockCount, StockCountItem
from schemas.inventory import (
    InventoryItemCreate, InventoryItemResponse,
    InvuStatusResponse, InvuSyncResult,
    SupplierCreate, SupplierResponse,
    ShipmentCreate, ShipmentResponse, ShipmentItemResponse,
    StockCountCreate, StockCountItemResponse, StockCountResponse,
    StockRowResponse, WasteCreate, WasteItemResponse, WasteReasonResponse, WasteResponse,
)
from services import invu_client, invu_sync
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

logger = logging.getLogger("farmhouse.inventory")

router = APIRouter(prefix="/inventory", tags=["Inventario"])

# Vocabulario de la merma. Lista cerrada y no texto libre porque el sentido del módulo es poder
# decir "este mes se perdió tanto por vencimiento": con motivos escritos a mano cada sucursal
# inventa el suyo y no suma nada. "Otro" existe para lo que no entra, y la nota recoge el detalle.
# El orden es el que se ve en el formulario: primero lo que más pasa.
WASTE_REASONS = (
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
    limit: int = Query(8, ge=1, le=200),
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
    if current_user.role == "agent" and shipment_in.branch_id != current_user.branch_id:
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


def _on_hand_map(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """Existencia actual (entradas - mermas + diferencias de conteo) de esos insumos en esa sucursal."""
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
    return {
        item_id: Decimal(entradas.get(item_id, 0)) - Decimal(salidas.get(item_id, 0)) + Decimal(ajustes.get(item_id, 0))
        for item_id in item_ids
    }


def _serialize_waste(record: WasteRecord, stock_before: Optional[dict] = None) -> WasteResponse:
    items: List[WasteItemResponse] = []
    total_cost = Decimal("0.00")
    has_cost = False
    negativos: List[str] = []

    for line in record.items:
        if line.unit_cost is not None:
            total_cost += (Decimal(line.quantity) * Decimal(line.unit_cost))
            has_cost = True

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
        negative_items=negativos,
    )


@router.get("/waste/reasons", response_model=List[WasteReasonResponse])
def list_waste_reasons(current_user: User = Depends(get_current_authorized_user)):
    """
    Los motivos vienen del servidor y no escritos en el frontend: el día que el negocio agregue
    uno, se agrega en un solo lugar y las pantallas y los reportes ya hablan el mismo idioma.
    """
    return [WasteReasonResponse(code=code, label=label) for code, label in WASTE_REASONS]


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
    if current_user.role == "agent" and waste_in.branch_id != current_user.branch_id:
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

    # La existencia se mira ANTES de grabar: después este mismo registro ya estaría restando.
    stock_before = _on_hand_map(db, waste_in.branch_id, item_ids)

    record = WasteRecord(
        branch_id=waste_in.branch_id,
        recorded_by_user_id=current_user.id,
        occurred_at=waste_in.occurred_at or datetime.now(timezone.utc),
        reason=waste_in.reason,
        notes=(waste_in.notes or None),
    )
    for line in waste_in.items:
        costo = line.unit_cost
        if costo is None:
            costo = _last_known_cost(db, waste_in.branch_id, line.inventory_item_id)
        record.items.append(WasteItem(
            inventory_item_id=line.inventory_item_id,
            quantity=line.quantity,
            unit_cost=costo,
        ))

    db.add(record)
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
    )

    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is not None:
        query = query.filter(WasteRecord.branch_id == efectiva)
    if reason:
        query = query.filter(WasteRecord.reason == reason)

    records = query.order_by(WasteRecord.occurred_at.desc(), WasteRecord.id.desc()).offset(offset).limit(limit).all()
    return [_serialize_waste(r) for r in records]


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
    salidas_q = (
        db.query(
            WasteItem.inventory_item_id.label("item_id"),
            func.coalesce(func.sum(WasteItem.quantity), 0).label("cantidad"),
            func.coalesce(func.sum(WasteItem.quantity * func.coalesce(WasteItem.unit_cost, 0)), 0).label("costo"),
            func.max(WasteRecord.occurred_at).label("ultimo"),
        )
        .join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id)
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
        if only_stocked and not entrada and not salida and not ajuste:
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
            on_hand=entro - salio + ajustado,
            wasted_cost=(Decimal(salida.costo).quantize(Decimal("0.01")) if salida and salida.costo else None),
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
    if current_user.role == "agent" and count_in.branch_id != current_user.branch_id:
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
