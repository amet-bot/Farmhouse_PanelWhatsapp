"""
Registro de consumo de insumos: el equipo anota cuánto se usó de cada cosa (en la unidad del
insumo) y eso descuenta la existencia de la sucursal. Es la salida que no pasa por recetas de
Invu (empaques, limpieza, insumos sin receta) o que se quiere llevar a mano en vez de estimarla.

Reglas de acceso: cualquier persona de la sucursal registra en la suya; admin y supervisor
global en cualquiera. Borrar: quien lo registró dentro de las 24 h, o un encargado
(inventory.adjust). Todo queda en auditoría.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.consumption import ConsumptionItem, ConsumptionRecord
from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from models.stock_count import StockCount, StockCountItem
from models.supply import ItemBranchSetting
from models.branch import Branch
from models.user import User
from routers.inventory import _existencia_map, _last_known_cost, _visible_branch_filter
from security.access_control import check_target_branch_valid
from security.auth import get_current_authorized_user
from security.permissions import has_permission
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.consumption")

router = APIRouter(prefix="/inventory", tags=["Consumo"])

DELETE_WINDOW_HOURS = 24


class ConsumptionLineIn(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0, max_digits=10, decimal_places=3)


class ConsumptionCreate(BaseModel):
    branch_id: int
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=500)
    items: List[ConsumptionLineIn] = Field(..., min_length=1, max_length=200)


class ConsumptionLineOut(BaseModel):
    id: int
    inventory_item_id: int
    item_name: str
    unit: str
    category: Optional[str] = None
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    stock_after: Optional[Decimal] = None


class ConsumptionOut(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    recorded_by_user_id: int
    recorded_by_name: str
    occurred_at: datetime
    notes: Optional[str] = None
    created_at: datetime
    total_cost: Optional[Decimal] = None
    can_delete: bool = False
    items: List[ConsumptionLineOut]


def _q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _require_branch(current_user: User, branch_id: int) -> None:
    es_global = current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)
    if not es_global and current_user.branch_id != branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permiso para registrar consumo en otra sucursal.")


def _puede_borrar(rec: ConsumptionRecord, user: User) -> bool:
    if has_permission(user, "inventory.adjust"):
        return True
    if rec.recorded_by_user_id != user.id:
        return False
    creado = rec.created_at.replace(tzinfo=None) if rec.created_at.tzinfo else rec.created_at
    return datetime.utcnow() - creado <= timedelta(hours=DELETE_WINDOW_HOURS)


def _out(rec: ConsumptionRecord, user: User, stock_after: Optional[dict] = None) -> ConsumptionOut:
    lineas = []
    total = Decimal("0")
    con_costo = False
    for li in rec.items:
        costo = _q(Decimal(li.quantity) * Decimal(li.unit_cost)) if li.unit_cost is not None else None
        if costo is not None:
            total += costo
            con_costo = True
        lineas.append(ConsumptionLineOut(
            id=li.id, inventory_item_id=li.inventory_item_id, item_name=li.inventory_item.name, unit=li.inventory_item.unit,
            category=li.inventory_item.category, quantity=li.quantity, unit_cost=li.unit_cost, cost=costo,
            stock_after=(stock_after or {}).get(li.inventory_item_id),
        ))
    return ConsumptionOut(
        id=rec.id, branch_id=rec.branch_id, branch_name=rec.branch.name,
        recorded_by_user_id=rec.recorded_by_user_id, recorded_by_name=rec.recorded_by_user.name,
        occurred_at=rec.occurred_at, notes=rec.notes, created_at=rec.created_at,
        total_cost=total if con_costo else None, can_delete=_puede_borrar(rec, user), items=lineas,
    )


@router.post("/consumption", response_model=ConsumptionOut, status_code=status.HTTP_201_CREATED)
def create_consumption(
    data: ConsumptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Anota lo que se usó. No bloquea si la existencia calculada queda en negativo (igual que
    la merma: el arranque de inventario puede no estar cargado); la respuesta trae la
    existencia que queda de cada insumo para que se vea en pantalla."""
    _require_branch(current_user, data.branch_id)
    check_target_branch_valid(db, data.branch_id)

    ids = [l.inventory_item_id for l in data.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hay un insumo repetido en la lista.")
    encontrados = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.id.in_(ids)).all()}
    faltan = set(ids) - set(encontrados)
    if faltan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ítem(s) de inventario no encontrados: {sorted(faltan)}")

    rec = ConsumptionRecord(
        branch_id=data.branch_id, recorded_by_user_id=current_user.id,
        occurred_at=data.occurred_at or datetime.now(timezone.utc), notes=(data.notes or None),
    )
    for l in data.items:
        costo = _last_known_cost(db, data.branch_id, l.inventory_item_id)
        if costo is None:
            costo = encontrados[l.inventory_item_id].reference_cost
        rec.items.append(ConsumptionItem(inventory_item_id=l.inventory_item_id, quantity=l.quantity, unit_cost=costo))
    db.add(rec)
    db.flush()
    log_audit_event(db, current_user.id, data.branch_id, "consumption.create", "consumption", rec.id,
                    {"items": len(data.items), "total_qty": str(sum((Decimal(l.quantity) for l in data.items), Decimal("0")))})
    db.commit()
    db.refresh(rec)
    logger.info(f"Consumo #{rec.id} en sucursal {rec.branch_id} por {current_user.name}: {len(rec.items)} insumo(s)")
    return _out(rec, current_user, _existencia_map(db, data.branch_id, ids))


@router.get("/consumption", response_model=List[ConsumptionOut])
def list_consumption(
    branch_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    efectiva = _visible_branch_filter(current_user, branch_id)
    q = db.query(ConsumptionRecord).options(
        joinedload(ConsumptionRecord.items).joinedload(ConsumptionItem.inventory_item),
        joinedload(ConsumptionRecord.branch), joinedload(ConsumptionRecord.recorded_by_user),
    )
    if efectiva is not None:
        q = q.filter(ConsumptionRecord.branch_id == efectiva)
    if date_from:
        q = q.filter(ConsumptionRecord.occurred_at >= datetime.combine(date_from, datetime.min.time()) + timedelta(hours=5))
    if date_to:
        q = q.filter(ConsumptionRecord.occurred_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()) + timedelta(hours=5))
    return [_out(r, current_user) for r in q.order_by(ConsumptionRecord.occurred_at.desc(), ConsumptionRecord.id.desc()).limit(limit).all()]


BOARD_DAYS = 30


def _inicio_dia_local(now_utc: datetime) -> datetime:
    """Medianoche de hoy en Panamá (UTC-5), expresada en UTC naive, igual que los filtros por fecha."""
    hoy = (now_utc - timedelta(hours=5)).date()
    return datetime.combine(hoy, datetime.min.time()) + timedelta(hours=5)


@router.get("/consumption/board")
def consumption_board(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Tablero para la tablet de la sucursal: todo el catálogo activo con lo que queda de cada
    insumo, su mínimo, lo que ya se anotó hoy, cuántas veces se anotó en los últimos 30 días
    (para poner primero lo frecuente) y la última cantidad que se anotó (para ofrecerla de un
    toque). `tracked` dice si ese insumo tiene algún movimiento en la sucursal: sin movimientos
    la existencia no es un cero real, es que nadie la cargó todavía.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Indica la sucursal.")
    sucursal = check_target_branch_valid(db, efectiva)

    items = db.query(InventoryItem).filter(InventoryItem.active == True).order_by(InventoryItem.name.asc()).all()
    ids = [i.id for i in items]
    stock = _existencia_map(db, efectiva, ids) if ids else {}
    minimos = {
        r.inventory_item_id: r.min_quantity
        for r in db.query(ItemBranchSetting).filter(ItemBranchSetting.branch_id == efectiva, ItemBranchSetting.inventory_item_id.in_(ids or [0])).all()
    }

    con_movimiento = set()
    con_movimiento.update(i for (i,) in db.query(ShipmentItem.inventory_item_id).join(Shipment, Shipment.id == ShipmentItem.shipment_id).filter(Shipment.branch_id == efectiva).distinct().all())
    con_movimiento.update(i for (i,) in db.query(StockCountItem.inventory_item_id).join(StockCount, StockCount.id == StockCountItem.stock_count_id).filter(StockCount.branch_id == efectiva).distinct().all())
    con_movimiento.update(i for (i,) in db.query(ConsumptionItem.inventory_item_id).join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id).filter(ConsumptionRecord.branch_id == efectiva).distinct().all())

    ahora = datetime.utcnow()
    inicio_hoy = _inicio_dia_local(ahora)
    recientes = (
        db.query(ConsumptionItem.inventory_item_id, ConsumptionItem.quantity, ConsumptionRecord.occurred_at)
        .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
        .filter(ConsumptionRecord.branch_id == efectiva, ConsumptionRecord.occurred_at >= ahora - timedelta(days=BOARD_DAYS))
        .order_by(ConsumptionRecord.occurred_at.desc(), ConsumptionItem.id.desc())
        .all()
    )
    veces: dict = {}
    ultima: dict = {}
    hoy: dict = {}
    for item_id, cantidad, cuando in recientes:
        veces[item_id] = veces.get(item_id, 0) + 1
        if item_id not in ultima:
            ultima[item_id] = {"qty": cantidad, "at": cuando}
        cuando_naive = cuando.replace(tzinfo=None) if cuando.tzinfo else cuando
        if cuando_naive >= inicio_hoy:
            hoy[item_id] = hoy.get(item_id, Decimal("0")) + Decimal(cantidad)

    filas = []
    for it in items:
        existencia = Decimal(stock.get(it.id, 0))
        minimo = minimos.get(it.id)
        filas.append({
            "inventory_item_id": it.id, "name": it.name, "unit": it.unit, "category": it.category,
            "tracked": it.id in con_movimiento,
            "stock": existencia if it.id in con_movimiento else None,
            "min_quantity": minimo,
            "below_min": bool(minimo is not None and it.id in con_movimiento and existencia < Decimal(minimo)),
            "today_qty": hoy.get(it.id, Decimal("0")),
            "times_30d": veces.get(it.id, 0),
            "last_qty": ultima[it.id]["qty"] if it.id in ultima else None,
            "last_at": ultima[it.id]["at"] if it.id in ultima else None,
        })
    categorias = sorted({f["category"] for f in filas if f["category"]})
    return {
        "branch": {"id": sucursal.id, "name": sucursal.name},
        "has_data": bool(con_movimiento),
        "items": filas,
        "categories": categorias,
        "days": BOARD_DAYS,
    }


@router.get("/consumption/summary")
def consumption_summary(
    branch_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Cuánto se usó de cada insumo en el período (solo lo registrado a mano), sumado."""
    efectiva = _visible_branch_filter(current_user, branch_id)
    q = db.query(
        ConsumptionItem.inventory_item_id, InventoryItem.name, InventoryItem.unit, InventoryItem.category,
        func.coalesce(func.sum(ConsumptionItem.quantity), 0),
        func.coalesce(func.sum(ConsumptionItem.quantity * func.coalesce(ConsumptionItem.unit_cost, 0)), 0),
        func.count(ConsumptionItem.id),
    ).join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id).join(InventoryItem, InventoryItem.id == ConsumptionItem.inventory_item_id)
    if efectiva is not None:
        q = q.filter(ConsumptionRecord.branch_id == efectiva)
    if date_from:
        q = q.filter(ConsumptionRecord.occurred_at >= datetime.combine(date_from, datetime.min.time()) + timedelta(hours=5))
    if date_to:
        q = q.filter(ConsumptionRecord.occurred_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()) + timedelta(hours=5))
    rows = [{"inventory_item_id": i, "name": n, "unit": u, "category": c, "quantity": _q(qty), "cost": _q(cost), "records": int(k)}
            for i, n, u, c, qty, cost, k in q.group_by(ConsumptionItem.inventory_item_id, InventoryItem.name, InventoryItem.unit, InventoryItem.category).all()]
    rows.sort(key=lambda r: (-r["cost"], -r["quantity"]))
    return {"rows": rows, "total_cost": sum((r["cost"] for r in rows), Decimal("0"))}


@router.delete("/consumption/{record_id}")
def delete_consumption(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    rec = db.query(ConsumptionRecord).filter(ConsumptionRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro de consumo no encontrado.")
    _require_branch(current_user, rec.branch_id)
    if not _puede_borrar(rec, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo quien lo registró (dentro de 24 h) o un encargado puede borrarlo.")
    log_audit_event(db, current_user.id, rec.branch_id, "consumption.delete", "consumption", rec.id, {"items": len(rec.items)})
    db.delete(rec)
    db.commit()
    return {"status": "deleted", "id": record_id}
