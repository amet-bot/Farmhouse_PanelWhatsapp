import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.inventory_item import InventoryItem
from models.supplier import Supplier
from models.shipment import Shipment, ShipmentItem
from models.user import User
from schemas.inventory import (
    InventoryItemCreate, InventoryItemResponse,
    SupplierCreate, SupplierResponse,
    ShipmentCreate, ShipmentResponse, ShipmentItemResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

logger = logging.getLogger("farmhouse.inventory")

router = APIRouter(prefix="/inventory", tags=["Inventario"])


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
    """Cualquier usuario logueado puede crear un proveedor nuevo (calcado de create_inventory_item)."""
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
