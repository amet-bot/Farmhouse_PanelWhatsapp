"""
Recepción de cargamentos: lo que rodea al registro del cargamento en sí (routers/inventory.py).

  - Cargamentos agendados: "Mañana llega PriceSmart, de 3 pm en adelante". Los agenda un
    supervisor, a la sucursal le llega un aviso y le aparece en el inicio; al recibirlo se enlaza
    con el cargamento real.
  - Fotos de la factura (o de lo que llegó mal), igual que las de merma.
  - Qué proveedor falla más: entregas con diferencias y cuánto faltó en $.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from database import SessionLocal, get_db
from models.branch import Branch
from models.shipment import ExpectedShipment, Shipment, ShipmentPhoto
from models.supplier import Supplier
from models.supply import ExpectedShipmentItem
from models.user import User
from routers.inventory import _image_type, _reclamo_de_linea, _serialize_shipment, _visible_branch_filter
from schemas.inventory import (
    ExpectedShipmentCreate, ExpectedShipmentLine, ExpectedShipmentResponse, ShipmentResponse, SupplierIssueRow,
)
from security.access_control import check_target_branch_valid
from security.auth import get_current_authorized_user
from security.permissions import require_permission
from services import invu_sales_sync, push_service
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.receiving")
router = APIRouter(prefix="/inventory", tags=["Inventario"])

SHIPMENT_PHOTO_MAX_BYTES = 8 * 1024 * 1024
SHIPMENT_PHOTOS_PER_RECORD = 4


# ==========================================================================
# Cargamentos agendados
# ==========================================================================
def _serialize_expected(e: ExpectedShipment) -> ExpectedShipmentResponse:
    lineas = [
        ExpectedShipmentLine(
            inventory_item_id=li.inventory_item_id, item_name=li.inventory_item.name, unit=li.inventory_item.unit,
            quantity=li.quantity, unit_cost=li.unit_cost,
        )
        for li in (e.items or [])
    ]
    costo = sum((Decimal(li.quantity) * Decimal(li.unit_cost) for li in (e.items or []) if li.unit_cost is not None), Decimal("0"))
    return ExpectedShipmentResponse(
        id=e.id, branch_id=e.branch_id, branch_name=e.branch.name,
        supplier_id=e.supplier_id, supplier_name=(e.supplier.name if e.supplier else None),
        expected_date=e.expected_date, time_from=e.time_from, notes=e.notes, status=e.status,
        created_by_name=(e.created_by_user.name if e.created_by_user else None),
        created_at=e.created_at, shipment_id=e.shipment_id,
        items=lineas, est_cost=(costo.quantize(Decimal("0.01")) if lineas and costo else None),
    )


def _hora_legible(hhmm: Optional[str]) -> Optional[str]:
    """"15:00" → "3:00 pm"."""
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def _avisar_agendado_background(branch_id: int, title: str, body: str, tag: str) -> None:
    db = SessionLocal()
    try:
        push_service.notify_branch_staff(db, branch_id, title, body, "/inventario?view=cargamentos", tag=tag)
    except Exception as e:  # un aviso fallido no deshace lo agendado
        logger.error(f"[Push agendado] sucursal {branch_id}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/expected-shipments", response_model=ExpectedShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_expected_shipment(
    data: ExpectedShipmentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Agenda un cargamento y le avisa a la sucursal (todos sus usuarios) por notificación."""
    efectiva = _visible_branch_filter(current_user, data.branch_id)
    if efectiva is not None and efectiva != data.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes agendar cargamentos en otra sucursal.")
    check_target_branch_valid(db, data.branch_id)
    proveedor = None
    if data.supplier_id is not None:
        proveedor = db.query(Supplier).filter(Supplier.id == data.supplier_id).first()
        if not proveedor:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El proveedor seleccionado no existe.")
    hoy = invu_sales_sync.hoy_panama()
    if data.expected_date < hoy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La fecha ya pasó.")

    e = ExpectedShipment(
        branch_id=data.branch_id, supplier_id=data.supplier_id, expected_date=data.expected_date,
        time_from=data.time_from, notes=((data.notes or "").strip() or None),
        status="pendiente", created_by_user_id=current_user.id,
    )
    db.add(e)
    db.flush()
    log_audit_event(db, current_user.id, e.branch_id, "expected_shipment.create", "expected_shipment", e.id,
                    {"supplier_id": e.supplier_id, "expected_date": e.expected_date.isoformat(), "time_from": e.time_from})
    db.commit()
    db.refresh(e)

    cuando = "Hoy" if e.expected_date == hoy else ("Mañana" if e.expected_date == hoy + timedelta(days=1)
                                                   else f"El {e.expected_date.strftime('%d/%m')}")
    quien = proveedor.name if proveedor else "un cargamento"
    hora = _hora_legible(e.time_from)
    cuerpo = f"{e.branch.name}{f' · desde las {hora}' if hora else ''}. Revisen todo contra la factura."
    if e.notes:
        cuerpo += f" {e.notes}"
    background_tasks.add_task(_avisar_agendado_background, e.branch_id, f"{cuando} llega {quien}", cuerpo,
                              f"fh-expected-{e.id}")
    return _serialize_expected(e)


@router.get("/expected-shipments", response_model=List[ExpectedShipmentResponse])
def list_expected_shipments(
    branch_id: Optional[int] = Query(None),
    status_filter: str = Query("pendiente", alias="status", pattern="^(pendiente|recibido|cancelado|todos)$"),
    days_ahead: int = Query(14, ge=0, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Lo agendado, lo más próximo primero. Los pendientes incluyen los atrasados (fecha pasada y
    todavía sin recibir): son justamente los que hay que ir a mirar.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)
    q = db.query(ExpectedShipment).options(
        selectinload(ExpectedShipment.branch), selectinload(ExpectedShipment.supplier),
        selectinload(ExpectedShipment.created_by_user),
        selectinload(ExpectedShipment.items).selectinload(ExpectedShipmentItem.inventory_item),
    )
    if efectiva is not None:
        q = q.filter(ExpectedShipment.branch_id == efectiva)
    hoy = invu_sales_sync.hoy_panama()
    if status_filter != "todos":
        q = q.filter(ExpectedShipment.status == status_filter)
    if status_filter == "pendiente":
        q = q.filter(ExpectedShipment.expected_date <= hoy + timedelta(days=days_ahead))
    else:
        q = q.filter(ExpectedShipment.expected_date >= hoy - timedelta(days=30))
    filas = q.order_by(ExpectedShipment.expected_date.asc(), ExpectedShipment.time_from.asc(), ExpectedShipment.id.asc()).limit(100).all()
    return [_serialize_expected(e) for e in filas]


@router.post("/expected-shipments/{expected_id}/cancel", response_model=ExpectedShipmentResponse)
def cancel_expected_shipment(
    expected_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    e = db.query(ExpectedShipment).filter(ExpectedShipment.id == expected_id).first()
    if not e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento agendado no encontrado.")
    efectiva = _visible_branch_filter(current_user, e.branch_id)
    if efectiva is not None and efectiva != e.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a esa sucursal.")
    if e.status != "pendiente":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Solo se cancela lo que todavía no se recibió.")
    e.status = "cancelado"
    e.cancelled_at = datetime.now(timezone.utc)
    log_audit_event(db, current_user.id, e.branch_id, "expected_shipment.cancel", "expected_shipment", e.id, {})
    db.commit()
    db.refresh(e)
    return _serialize_expected(e)


# ==========================================================================
# Fotos de la factura
# ==========================================================================
def _shipment_for_user(db: Session, shipment_id: int, current_user: User) -> Shipment:
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cargamento no encontrado.")
    efectiva = _visible_branch_filter(current_user, shipment.branch_id)
    if efectiva is not None and efectiva != shipment.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a los cargamentos de otra sucursal.")
    return shipment


@router.post("/shipments/{shipment_id}/photos", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
async def add_shipment_photo(
    shipment_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Agrega una foto (factura o producto) a un cargamento. JPG, PNG o WebP, verificado por su contenido."""
    shipment = _shipment_for_user(db, shipment_id, current_user)
    if len(shipment.photos) >= SHIPMENT_PHOTOS_PER_RECORD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Un cargamento admite hasta {SHIPMENT_PHOTOS_PER_RECORD} fotos.")
    data = await file.read(SHIPMENT_PHOTO_MAX_BYTES + 1)
    if len(data) > SHIPMENT_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="La foto supera los 8 MB.")
    content_type = _image_type(data)
    if not content_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Solo se aceptan fotos (JPG, PNG o WebP).")
    shipment.photos.append(ShipmentPhoto(
        content_type=content_type, size_bytes=len(data), data=data, uploaded_by_user_id=current_user.id,
    ))
    log_audit_event(db, current_user.id, shipment.branch_id, "shipment.photo_add", "shipment", shipment.id,
                    {"size_bytes": len(data), "content_type": content_type})
    db.commit()
    db.refresh(shipment)
    return _serialize_shipment(shipment)


@router.get("/shipments/{shipment_id}/photos/{photo_id}")
def get_shipment_photo(
    shipment_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    shipment = _shipment_for_user(db, shipment_id, current_user)
    photo = db.query(ShipmentPhoto).filter(ShipmentPhoto.id == photo_id, ShipmentPhoto.shipment_id == shipment.id).first()
    if not photo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto no encontrada.")
    return Response(
        content=photo.data, media_type=photo.content_type,
        headers={"Cache-Control": "private, max-age=86400", "Content-Security-Policy": "sandbox",
                 "X-Content-Type-Options": "nosniff"},
    )


# ==========================================================================
# Qué proveedor falla más
# ==========================================================================
@router.get("/suppliers/issues", response_model=List[SupplierIssueRow])
def supplier_issues(
    days: int = Query(90, ge=7, le=365),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Por proveedor, en los cargamentos recibidos contra factura: cuántos llegaron con diferencias
    y cuánto faltó en $. Los que no se compararon contra factura no cuentan (no hay con qué).
    Primero el que más falla.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    q = db.query(Shipment).options(selectinload(Shipment.items), selectinload(Shipment.supplier)).filter(
        Shipment.has_issues.isnot(None), Shipment.received_at >= desde,
    )
    if efectiva is not None:
        q = q.filter(Shipment.branch_id == efectiva)
    por_proveedor: dict = {}
    for s in q.all():
        fila = por_proveedor.setdefault(s.supplier_id, {
            "nombre": s.supplier.name if s.supplier else "Sin proveedor", "total": 0, "fallas": 0,
            "reclamo": Decimal("0"), "ultima": None,
        })
        fila["total"] += 1
        if s.has_issues:
            fila["fallas"] += 1
            if fila["ultima"] is None or s.received_at > fila["ultima"]:
                fila["ultima"] = s.received_at
        fila["reclamo"] += sum((_reclamo_de_linea(l) or Decimal("0") for l in s.items), Decimal("0"))
    filas = [
        SupplierIssueRow(
            supplier_id=sid, supplier_name=v["nombre"], shipments=v["total"], with_issues=v["fallas"],
            issue_pct=(Decimal(v["fallas"]) / Decimal(v["total"]) * 100).quantize(Decimal("0.1")),
            claim_value=v["reclamo"].quantize(Decimal("0.01")), last_issue_at=v["ultima"],
        )
        for sid, v in por_proveedor.items()
    ]
    filas.sort(key=lambda f: (f.with_issues, f.claim_value), reverse=True)
    return filas
