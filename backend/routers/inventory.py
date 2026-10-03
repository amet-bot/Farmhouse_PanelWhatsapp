import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from database import SessionLocal, get_db
from models.branch import Branch
from models.inventory_item import InventoryItem, KIND_HOUSE, KIND_RAW
from models.invu_sales import InvuRecipeLine, InvuSale, InvuSaleLine, InvuSaleModifier, InvuSyncDay
from models.supplier import Supplier
from models.ops import Incident
from models.native_push import NativePushToken
from models.push_subscription import PushSubscription
from models.shipment import ExpectedShipment, Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem, WastePhoto
from models.stock_count import StockCount, StockCountItem
from models.inventory_movement import InventoryMovement
from models.transfer import Transfer, TransferItem
from models.consumption import ConsumptionRecord, ConsumptionItem
from schemas.inventory import (
    InventoryItemCreate, InventoryItemDensity, InventoryItemPieceSize, InventoryItemResponse,
    InvuStatusResponse, InvuSyncResult,
    SupplierCreate, SupplierResponse,
    SHIPMENT_LINE_STATUSES, ShipmentCreate, ShipmentPhotoResponse, ShipmentResponse, ShipmentItemResponse,
    ShipmentInsightItem, ShipmentInsights,
    StockCountCreate, StockCountItemResponse, StockCountResponse,
    StockCountAnalysis, StockCountAnalysisLine, StockCountAnalysisTotals,
    DashboardBranch, DashboardFigures, DashboardResponse, DashboardTopItem,
    StockRowResponse, WasteCreate, WasteItemResponse, WastePhotoResponse, WasteReasonResponse, WasteResponse,
    WasteInsightItem, WasteInsights,
    WasteAnalyticsGroup, WasteAnalyticsItem, WasteAnalyticsDay, WasteAnalyticsResponse, WasteAnalyticsTotals, WasteAnalyticsYield,
    WasteRecipeDish, WasteRecipeDishShare, WasteRecipeUsageItem, WasteRecipeUsageResponse,
    MovementComparisonResponse,
)
from config import settings
from services import fcm_service, invu_client, invu_items_sync, invu_recipes_sync, invu_sales_sync, invu_sync, push_service
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
    ("recorte", "Residuo o recorte al limpiar"),
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


def _reclamo_de_linea(line: ShipmentItem) -> Optional[Decimal]:
    """Lo que hay que reclamarle al proveedor por un renglón: (facturado − llegó) × costo."""
    if line.invoiced_quantity is None or line.unit_cost is None:
        return None
    falta = Decimal(line.invoiced_quantity) - Decimal(line.quantity)
    if falta <= 0:
        return None
    return (falta * Decimal(line.unit_cost)).quantize(Decimal("0.01"))


def _linea_con_problema(line: ShipmentItem) -> bool:
    return line.line_status not in (None, "ok")


def _serialize_shipment(shipment: Shipment) -> ShipmentResponse:
    items: List[ShipmentItemResponse] = []
    total_cost = Decimal("0.00")
    has_cost = False
    reclamo_total = Decimal("0")
    for line in shipment.items:
        if line.unit_cost is not None:
            total_cost += (Decimal(line.quantity) * Decimal(line.unit_cost))
            has_cost = True
        reclamo = _reclamo_de_linea(line)
        reclamo_total += reclamo or Decimal("0")
        items.append(ShipmentItemResponse(
            id=line.id,
            inventory_item_id=line.inventory_item_id,
            item_name=line.inventory_item.name,
            unit=line.inventory_item.unit,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            invoiced_quantity=line.invoiced_quantity,
            line_status=line.line_status,
            line_note=line.line_note,
            claim_value=reclamo,
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
        invoice_number=shipment.invoice_number,
        has_issues=shipment.has_issues,
        issues_count=sum(1 for l in shipment.items if _linea_con_problema(l)),
        claim_total=(reclamo_total.quantize(Decimal("0.01")) if reclamo_total else None),
        incident_id=shipment.incident_id,
        expected_shipment_id=(shipment.expected_shipment.id if shipment.expected_shipment else None),
        photos=[
            ShipmentPhotoResponse(
                id=p.id, content_type=p.content_type, size_bytes=p.size_bytes,
                uploaded_by_name=None, created_at=p.created_at,
            ) for p in shipment.photos
        ],
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


@router.post("/shipments", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(
    shipment_in: ShipmentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Registra lo que llegó. Recibir contra factura es opcional por renglón: con lo facturado, el
    servidor deduce si faltó o sobró; "equivocado" y "dañado" los marca quien recibe. `quantity`
    es siempre lo que entró de verdad a la existencia (puede ser 0 si no llegó nada de eso).

    Si algún renglón no llegó como decía la factura se abre sola una incidencia en Operación de
    Sucursal y se avisa por notificación a los encargados, sin depender del grupo de WhatsApp.
    """
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

    esperado = None
    if shipment_in.expected_shipment_id is not None:
        esperado = db.query(ExpectedShipment).filter(ExpectedShipment.id == shipment_in.expected_shipment_id).first()
        if not esperado or esperado.branch_id != shipment_in.branch_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El cargamento agendado no existe en esta sucursal.")
        if esperado.status != "pendiente":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese cargamento agendado ya se recibió o se canceló.")

    nombres = {i.id: i.name for i in found_items}
    estados: List[Optional[str]] = []
    for line in shipment_in.items:
        nombre = nombres[line.inventory_item_id]
        estado = (line.line_status or "").strip().lower() or None
        if estado is not None and estado not in SHIPMENT_LINE_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{nombre}: estado no válido.")
        if estado in ("equivocado", "danado"):
            pass
        elif line.invoiced_quantity is not None:
            q, fac = Decimal(line.quantity), Decimal(line.invoiced_quantity)
            estado = "falto" if q < fac else ("sobro" if q > fac else "ok")
        elif estado in ("falto", "sobro"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{nombre}: para decir que faltó o sobró, anotá cuánto dice la factura.")
        if Decimal(line.quantity) == 0 and estado not in ("falto", "equivocado", "danado"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{nombre}: la cantidad que llegó no puede ser cero.")
        estados.append(estado)
    comparado = bool(shipment_in.invoice_number) or any(e is not None for e in estados)

    shipment = Shipment(
        branch_id=shipment_in.branch_id,
        received_by_user_id=current_user.id,
        received_at=shipment_in.received_at or datetime.now(timezone.utc),
        supplier_id=shipment_in.supplier_id,
        notes=(shipment_in.notes or None),
        invoice_number=((shipment_in.invoice_number or "").strip() or None),
    )
    for line, estado in zip(shipment_in.items, estados):
        shipment.items.append(ShipmentItem(
            inventory_item_id=line.inventory_item_id,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            invoiced_quantity=line.invoiced_quantity,
            line_status=(estado or ("ok" if comparado else None)),
            line_note=((line.line_note or "").strip() or None),
        ))
    problemas = [l for l in shipment.items if _linea_con_problema(l)]
    shipment.has_issues = bool(problemas) if comparado else None

    db.add(shipment)
    db.flush()  # asigna shipment.id antes de generar los movimientos del libro (Fase 4)
    for line in shipment.items:
        if Decimal(line.quantity) == 0:
            continue   # no llegó nada de eso: no hay movimiento que anotar
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
    aviso = None
    if problemas:
        proveedor = db.query(Supplier.name).filter(Supplier.id == shipment.supplier_id).scalar() if shipment.supplier_id else None
        lineas = [_describir_problema(l) for l in problemas]
        reclamo = sum((_reclamo_de_linea(l) or Decimal("0") for l in problemas), Decimal("0"))
        grave = any(l.line_status in ("equivocado", "danado") for l in problemas) or reclamo >= Decimal("50")
        titulo = f"Cargamento con diferencias{f' · {proveedor}' if proveedor else ''}"[:150]
        detalle = "\n".join(f"- {x}" for x in lineas)
        if shipment.invoice_number:
            detalle = f"Factura {shipment.invoice_number}\n" + detalle
        if reclamo:
            detalle += f"\nPara reclamar: ${reclamo.quantize(Decimal('0.01'))}"
        incidencia = Incident(
            branch_id=shipment.branch_id, reported_by_user_id=current_user.id,
            title=titulo, description=f"Cargamento #{shipment.id}\n{detalle}",
            severity=("alta" if grave else "media"),
        )
        db.add(incidencia)
        db.flush()
        shipment.incident_id = incidencia.id
        sucursal = db.query(Branch.name).filter(Branch.id == shipment.branch_id).scalar()
        aviso = (
            f"Cargamento con diferencias · {sucursal}",
            f"{proveedor + ': ' if proveedor else ''}" + "; ".join(lineas),
            f"/inventario?view=cargamentos&shipment={shipment.id}",
            f"fh-shipment-{shipment.id}",
        )
    if esperado is not None:
        esperado.status = "recibido"
        esperado.shipment_id = shipment.id

    log_audit_event(
        db, current_user.id, shipment.branch_id, "shipment.create", "shipment", shipment.id,
        {"items": len(shipment.items), "supplier_id": shipment.supplier_id,
         "has_issues": shipment.has_issues, "incident_id": shipment.incident_id,
         "expected_shipment_id": esperado.id if esperado else None}
    )
    db.commit()
    db.refresh(shipment)
    logger.info(f"Cargamento #{shipment.id} registrado en sucursal {shipment.branch_id} por {current_user.name}"
                + (f" con {len(problemas)} diferencia(s)" if problemas else ""))
    respuesta = _serialize_shipment(shipment)
    respuesta.insights = _shipment_insights(db, shipment)
    if aviso is not None:
        respuesta.notified = _hay_a_quien_avisar(db, shipment.branch_id)
        background_tasks.add_task(_avisar_diferencias_background, shipment.branch_id, *aviso)
    return respuesta


def _shipment_insights(db: Session, shipment: Shipment) -> ShipmentInsights:
    """
    Pone el cargamento en contexto. Por insumo: el precio contra la compra anterior en esta
    sucursal, contra lo más barato que pagó otra sucursal hace poco y contra el costo de Invu; la
    existencia de hoy y, con recetas, para cuántos días alcanza. Y el gasto: la semana de la
    sucursal contra la anterior y el mes con este proveedor. Como en la merma, "hoy" es el día en
    que se recibió.
    """
    tz = invu_sales_sync.PANAMA_TZ
    recibido = shipment.received_at if shipment.received_at.tzinfo else shipment.received_at.replace(tzinfo=timezone.utc)
    dia = recibido.astimezone(tz).date()
    semana = _dias_utc(dia - timedelta(days=6), dia)
    previa = _dias_utc(dia - timedelta(days=13), dia - timedelta(days=7))
    mes = _dias_utc(dia - timedelta(days=29), dia)
    noventa = _dias_utc(dia - timedelta(days=89), dia)
    recibido_utc = recibido.astimezone(timezone.utc).replace(tzinfo=None)

    def gasto(rango, *extra):
        valor = db.query(func.coalesce(func.sum(ShipmentItem.quantity * ShipmentItem.unit_cost), 0)).join(
            Shipment, Shipment.id == ShipmentItem.shipment_id
        ).filter(
            Shipment.branch_id == shipment.branch_id,
            Shipment.received_at >= rango[0], Shipment.received_at < rango[1],
            ShipmentItem.unit_cost.isnot(None), *extra,
        ).scalar()
        return Decimal(valor or 0).quantize(Decimal("0.01"))

    item_ids = [l.inventory_item_id for l in shipment.items]
    existencias = _existencia_map(db, shipment.branch_id, item_ids)
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    uso14 = _uso_por_ventas(db, shipment.branch_id, ahora - timedelta(days=14), ahora)
    con_receta = _insumos_con_receta(db, shipment.branch_id)

    items: List[ShipmentInsightItem] = []
    sin_costo = 0
    for line in shipment.items:
        item = line.inventory_item
        costo = Decimal(line.unit_cost) if line.unit_cost is not None else None
        if costo is None:
            sin_costo += 1

        anterior = db.query(ShipmentItem, Shipment).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
            Shipment.branch_id == shipment.branch_id,
            ShipmentItem.inventory_item_id == item.id,
            ShipmentItem.unit_cost.isnot(None),
            Shipment.id != shipment.id,
            Shipment.received_at <= recibido_utc,
        ).order_by(Shipment.received_at.desc(), Shipment.id.desc()).first()
        prev_costo = Decimal(anterior[0].unit_cost) if anterior else None
        cambio = ((costo - prev_costo) / prev_costo * 100).quantize(Decimal("0.1")) if (costo and prev_costo) else None

        # Lo último que pagó cada otra sucursal en 90 días; se queda con lo más barato.
        otras = db.query(ShipmentItem, Shipment).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(
            Shipment.branch_id != shipment.branch_id,
            ShipmentItem.inventory_item_id == item.id,
            ShipmentItem.unit_cost.isnot(None),
            Shipment.received_at >= noventa[0], Shipment.received_at < noventa[1],
        ).order_by(Shipment.received_at.desc(), Shipment.id.desc()).all()
        ultima_por_sucursal: dict = {}
        for si, sh in otras:
            ultima_por_sucursal.setdefault(sh.branch_id, (si, sh))
        mejor = min(ultima_por_sucursal.values(), key=lambda par: Decimal(par[0].unit_cost), default=None)

        stock = Decimal(existencias.get(item.id, Decimal("0")))
        por_dia = (uso14.get(item.id, Decimal("0")) / 14) if item.id in con_receta else None
        items.append(ShipmentInsightItem(
            inventory_item_id=item.id, name=item.name, unit=item.unit,
            quantity=line.quantity, unit_cost=line.unit_cost,
            prev_unit_cost=prev_costo,
            prev_received_at=(anterior[1].received_at if anterior else None),
            prev_supplier=(anterior[1].supplier.name if anterior and anterior[1].supplier else None),
            change_pct=cambio,
            best_other_cost=(Decimal(mejor[0].unit_cost) if mejor else None),
            best_other_branch=(mejor[1].branch.name if mejor else None),
            best_other_supplier=(mejor[1].supplier.name if mejor and mejor[1].supplier else None),
            best_other_received_at=(mejor[1].received_at if mejor else None),
            reference_cost=item.reference_cost,
            stock_now=stock.quantize(Decimal("0.001")),
            used_per_day=(por_dia.quantize(Decimal("0.001")) if por_dia is not None else None),
            days_left=((stock / por_dia).quantize(Decimal("0.1")) if por_dia and por_dia > 0 and stock > 0 else None),
        ))

    # Historial del proveedor, todas las sucursales, 90 días: cuántas entregas comparadas contra
    # factura vinieron con diferencias y cuánto faltó en $.
    prov_total = prov_fallas = None
    prov_reclamo = None
    if shipment.supplier_id:
        comparados = db.query(Shipment).options(selectinload(Shipment.items)).filter(
            Shipment.supplier_id == shipment.supplier_id,
            Shipment.has_issues.isnot(None),
            Shipment.received_at >= noventa[0], Shipment.received_at < noventa[1],
        ).all()
        prov_total = len(comparados)
        prov_fallas = sum(1 for s in comparados if s.has_issues)
        prov_reclamo = sum((_reclamo_de_linea(l) or Decimal("0") for s in comparados for l in s.items),
                           Decimal("0")).quantize(Decimal("0.01"))

    total = None
    if any(l.unit_cost is not None for l in shipment.items):
        total = sum((Decimal(l.quantity) * Decimal(l.unit_cost) for l in shipment.items if l.unit_cost is not None), Decimal("0")).quantize(Decimal("0.01"))
    return ShipmentInsights(
        shipment_id=shipment.id, branch_id=shipment.branch_id, branch_name=shipment.branch.name,
        supplier_name=(shipment.supplier.name if shipment.supplier else None),
        total_cost=total,
        branch_week_spend=gasto(semana), branch_prev_week_spend=gasto(previa),
        supplier_month_spend=(gasto(mes, Shipment.supplier_id == shipment.supplier_id) if shipment.supplier_id else None),
        items_without_cost=sin_costo, items=items,
        supplier_shipments_90d=prov_total, supplier_issues_90d=prov_fallas, supplier_claim_90d=prov_reclamo,
    )


@router.delete("/shipments/{shipment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shipment(
    shipment_id: int,
    motivo: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Borra un cargamento cargado por error (mal la cantidad, el costo o repetido), con sus líneas y
    sus movimientos del libro: la existencia y las compras vuelven a quedar como si no se hubiera
    cargado. Mismas reglas que la merma: supervisor y admin cualquiera de sus sucursales, quien lo
    registró el suyo durante 24 horas. No se borra si después se contaron esos insumos (ver
    _chequear_sin_conteo_posterior). La auditoría guarda una copia de lo borrado y el motivo.
    """
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento no encontrado.")
    efectiva = _visible_branch_filter(current_user, shipment.branch_id)
    if efectiva is not None and efectiva != shipment.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los cargamentos de otra sucursal.")
    _chequear_quien_borra(current_user, shipment.received_by_user_id, shipment.created_at, "el cargamento")
    _chequear_sin_conteo_posterior(db, shipment.branch_id, [l.inventory_item_id for l in shipment.items],
                                   shipment.created_at, "este cargamento")

    snapshot = {
        "received_at": shipment.received_at.isoformat() if shipment.received_at else None,
        "received_by_user_id": shipment.received_by_user_id,
        "supplier_id": shipment.supplier_id,
        "notes": shipment.notes,
        "items": [
            {"inventory_item_id": l.inventory_item_id, "quantity": str(l.quantity),
             "unit_cost": str(l.unit_cost) if l.unit_cost is not None else None}
            for l in shipment.items
        ],
        "delete_reason": (motivo or "").strip() or None,
    }
    snapshot["invoice_number"] = shipment.invoice_number
    snapshot["incident_id"] = shipment.incident_id
    db.query(InventoryMovement).filter(
        InventoryMovement.source_type == "shipment", InventoryMovement.source_id == shipment.id,
    ).delete(synchronize_session=False)
    # Si se estaba recibiendo un cargamento agendado, vuelve a esperarse.
    db.query(ExpectedShipment).filter(ExpectedShipment.shipment_id == shipment.id).update(
        {ExpectedShipment.status: "pendiente", ExpectedShipment.shipment_id: None}, synchronize_session=False)
    log_audit_event(db, current_user.id, shipment.branch_id, "shipment.delete", "shipment", shipment.id, snapshot)
    db.delete(shipment)   # las líneas se van con él (cascade)
    db.commit()
    logger.info(f"Cargamento #{shipment_id} borrado por {current_user.name}" + (f" — {snapshot['delete_reason']}" if snapshot["delete_reason"] else ""))
    return None


@router.get("/shipments/{shipment_id}/insights", response_model=ShipmentInsights)
def shipment_insights(
    shipment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento no encontrado.")
    efectiva = _visible_branch_filter(current_user, shipment.branch_id)
    if efectiva is not None and efectiva != shipment.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los cargamentos de otra sucursal.")
    return _shipment_insights(db, shipment)


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


def _dias_utc(desde: date, hasta: date) -> tuple:
    """[desde 00:00, hasta+1 00:00) de Panamá, en UTC sin zona (como se guarda todo)."""
    tz = invu_sales_sync.PANAMA_TZ
    ini = datetime.combine(desde, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return ini, fin


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

    costo_linea = WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.reference_cost, 0)

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
        costo = line.unit_cost if line.unit_cost is not None else item.reference_cost
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
            cost_estimated=line.unit_cost is None and item.reference_cost is not None,
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
    Existencias por insumo: entró, salió por merma, se corrigió por conteo, se vendió desde el
    último conteo (ventas de Invu × recetas), y lo que queda.

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
    # Lo vendido desde el último conteo, por sucursal (cada una tiene sus recetas y sus conteos);
    # sumando todas, se suma lo de cada una.
    vendidos: dict = {}
    sucursales = [efectiva] if efectiva is not None else [b.id for b in db.query(Branch.id).all()]
    for b_id in sucursales:
        for item_id, cantidad in _vendido_desde_conteo(db, b_id).items():
            vendidos[item_id] = vendidos.get(item_id, Decimal("0")) + cantidad

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
        vendido = vendidos.get(item.id)
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
            sold_since_count=(vendido.quantize(Decimal("0.001")) if vendido else None),
            on_hand=entro - salio + ajustado + trasladado - (vendido or Decimal("0")),
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
    con_receta = _insumos_con_receta(db, record.branch_id)

    totales = StockCountAnalysisTotals(items=len(record.items))
    lineas: List[StockCountAnalysisLine] = []
    for line in record.items:
        item = line.inventory_item
        contado = Decimal(line.counted_quantity)
        sistema = Decimal(line.expected_quantity)
        costo = Decimal(line.unit_cost) if line.unit_cost is not None and Decimal(line.unit_cost) > 0 else None
        estimado = False
        if costo is None and item.reference_cost is not None:
            costo, estimado = Decimal(item.reference_cost), True

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
        func.sum(WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.reference_cost, 0)),
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


@router.get("/waste/{waste_id:int}", response_model=WasteResponse)
def get_waste(
    waste_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Una merma (si quien pregunta ve esa sucursal): la abre el aviso de "merma importante"."""
    return _serialize_waste(_waste_for_user(db, waste_id, current_user))
