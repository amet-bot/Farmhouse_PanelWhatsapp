"""
Hoja de cierre de turno: la forma simple de que la sucursal lleve el gasto de insumos.

En vez de pedirle al operario que recuerde cuánto usó, la tablet le muestra siempre la misma
lista (la arma el encargado una vez) y él escribe cuánto QUEDA de cada cosa mirando el estante.
El sistema calcula solo lo que se gastó: lo que había la última vez (más lo que llegó) menos lo
que hay ahora.

Por dentro cada cierre es un conteo (StockCount, kind='closing') sobre los insumos de la hoja:
la existencia pasa a ser lo contado y la diferencia contra los registros es el gasto del turno.
Así no hay dos formas de calcular existencia, y el ritmo de uso para el pedido sugerido sale de
los cierres (ver supply.usage_per_day).
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from models.stock_count import StockCount, StockCountItem
from models.supply import ItemBranchSetting
from models.user import User
from routers.inventory import _visible_branch_filter, create_count
from schemas.inventory import StockCountCreate, StockCountItemCreate
from security.access_control import check_target_branch_valid
from security.auth import get_current_authorized_user
from security.permissions import has_permission, require_permission
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.closing_sheet")

router = APIRouter(prefix="/inventory/closing-sheet", tags=["Hoja de cierre"])

COUNT_KIND_CLOSING = "closing"
MAX_SHEET_ITEMS = 200


class SheetConfigIn(BaseModel):
    branch_id: int
    # En el orden en que deben aparecer en la hoja.
    item_ids: List[int] = Field(..., max_length=MAX_SHEET_ITEMS)


class SheetLineIn(BaseModel):
    inventory_item_id: int
    counted_quantity: Decimal = Field(..., ge=0, max_digits=10, decimal_places=3)


class SheetSubmitIn(BaseModel):
    branch_id: int
    notes: Optional[str] = Field(None, max_length=500)
    lines: List[SheetLineIn] = Field(..., min_length=1, max_length=MAX_SHEET_ITEMS)


def _q3(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.001"))


def _branch_for(current_user: User, branch_id: Optional[int], db: Session) -> int:
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Indica la sucursal.")
    check_target_branch_valid(db, efectiva)
    return efectiva


def _sheet_settings(db: Session, branch_id: int) -> List[ItemBranchSetting]:
    return (
        db.query(ItemBranchSetting)
        .options(joinedload(ItemBranchSetting.inventory_item))
        .filter(ItemBranchSetting.branch_id == branch_id, ItemBranchSetting.on_closing_sheet == True)
        .order_by(ItemBranchSetting.sheet_position.asc(), ItemBranchSetting.id.asc())
        .all()
    )


def _last_counts(db: Session, branch_id: int, item_ids: List[int]) -> dict:
    """Último conteo (de cualquier tipo) de cada insumo en la sucursal: cantidad y momento."""
    if not item_ids:
        return {}
    ultimos = dict(
        db.query(StockCountItem.inventory_item_id, func.max(StockCount.counted_at))
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(StockCount.branch_id == branch_id, StockCountItem.inventory_item_id.in_(item_ids))
        .group_by(StockCountItem.inventory_item_id).all()
    )
    if not ultimos:
        return {}
    filas = (
        db.query(StockCountItem.inventory_item_id, StockCountItem.counted_quantity, StockCountItem.difference, StockCount.counted_at)
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(StockCount.branch_id == branch_id, StockCountItem.inventory_item_id.in_(list(ultimos)))
        .order_by(StockCount.counted_at.desc(), StockCountItem.id.desc())
        .all()
    )
    out: dict = {}
    veces: dict = {}
    for item_id, cantidad, diferencia, cuando in filas:
        veces[item_id] = veces.get(item_id, 0) + 1
        if item_id not in out and cuando == ultimos[item_id]:
            out[item_id] = {"qty": cantidad, "at": cuando, "used": -Decimal(diferencia)}
    # El primer conteo de un insumo no dice cuánto se usó: su diferencia es la existencia de arranque.
    for item_id, info in out.items():
        if veces.get(item_id, 0) < 2:
            info["used"] = None
    return out


def _received_since(db: Session, branch_id: int, ultimos: dict) -> dict:
    """Lo que llegó por cargamento de cada insumo desde su último conteo (para que el operario
    entienda por qué puede haber más que la última vez)."""
    out: dict = {}
    if not ultimos:
        return out
    grupos: dict = {}
    for item_id, info in ultimos.items():
        grupos.setdefault(info["at"], []).append(item_id)
    for desde, ids in grupos.items():
        filas = (
            db.query(ShipmentItem.inventory_item_id, func.coalesce(func.sum(ShipmentItem.quantity), 0))
            .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
            .filter(Shipment.branch_id == branch_id, Shipment.received_at > desde, ShipmentItem.inventory_item_id.in_(ids))
            .group_by(ShipmentItem.inventory_item_id).all()
        )
        for item_id, cantidad in filas:
            out[item_id] = Decimal(cantidad)
    return out


def _last_closing(db: Session, branch_id: int) -> Optional[StockCount]:
    return (
        db.query(StockCount).options(joinedload(StockCount.counted_by_user), joinedload(StockCount.items))
        .filter(StockCount.branch_id == branch_id, StockCount.kind == COUNT_KIND_CLOSING)
        .order_by(StockCount.counted_at.desc(), StockCount.id.desc()).first()
    )


@router.get("")
def get_sheet(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """La hoja de la sucursal: sus insumos en orden, con lo que quedó la última vez y lo que llegó
    desde entonces. No manda lo que el sistema cree que hay ahora: la idea es que el operario
    cuente de verdad, no que copie un número."""
    efectiva = _branch_for(current_user, branch_id, db)
    ajustes = _sheet_settings(db, efectiva)
    ids = [a.inventory_item_id for a in ajustes if a.inventory_item.active]
    ultimos = _last_counts(db, efectiva, ids)
    llegaron = _received_since(db, efectiva, ultimos)
    ultimo_cierre = _last_closing(db, efectiva)

    items = []
    for a in ajustes:
        it = a.inventory_item
        if not it.active:
            continue
        u = ultimos.get(it.id)
        items.append({
            "inventory_item_id": it.id, "name": it.name, "unit": it.unit, "category": it.category,
            "position": a.sheet_position,
            "last_counted_qty": u["qty"] if u else None,
            "last_counted_at": u["at"] if u else None,
            # Lo que se usó entre el penúltimo y el último conteo (None si solo se contó una vez).
            "last_used": (u["used"] if u else None),
            "received_since": llegaron.get(it.id, Decimal("0")),
        })
    from models.branch import Branch
    sucursal = db.get(Branch, efectiva)
    return {
        "branch": {"id": sucursal.id, "name": sucursal.name},
        "configured": bool(items),
        "can_configure": has_permission(current_user, "inventory.adjust"),
        "items": items,
        "last_closing": None if not ultimo_cierre else {
            "id": ultimo_cierre.id, "at": ultimo_cierre.counted_at, "by": ultimo_cierre.counted_by_user.name,
            "items": len(ultimo_cierre.items), "notes": ultimo_cierre.notes,
        },
    }


@router.put("/config")
def save_sheet_config(
    data: SheetConfigIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Qué insumos van en la hoja de esa sucursal y en qué orden. Reemplaza la lista entera."""
    efectiva = _visible_branch_filter(current_user, data.branch_id)
    if efectiva is None or efectiva != data.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes armar la hoja de otra sucursal.")
    check_target_branch_valid(db, efectiva)
    ids = list(dict.fromkeys(data.item_ids))
    if ids:
        existentes = {i.id for i in db.query(InventoryItem.id).filter(InventoryItem.id.in_(ids)).all()}
        faltan = set(ids) - existentes
        if faltan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ítem(s) de inventario no encontrados: {sorted(faltan)}")

    actuales = {s.inventory_item_id: s for s in db.query(ItemBranchSetting).filter(ItemBranchSetting.branch_id == efectiva).all()}
    for s in actuales.values():
        if s.on_closing_sheet and s.inventory_item_id not in ids:
            s.on_closing_sheet = False
            s.sheet_position = None
            s.updated_by_user_id = current_user.id
    for pos, item_id in enumerate(ids, start=1):
        s = actuales.get(item_id)
        if s is None:
            s = ItemBranchSetting(inventory_item_id=item_id, branch_id=efectiva)
            db.add(s)
        s.on_closing_sheet = True
        s.sheet_position = pos
        s.updated_by_user_id = current_user.id
    log_audit_event(db, current_user.id, efectiva, "closing_sheet.config", "branch", efectiva, {"items": len(ids)})
    db.commit()
    return {"status": "ok", "branch_id": efectiva, "items": len(ids)}


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_sheet(
    data: SheetSubmitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Guarda el cierre: lo que quedó de cada insumo. Crea un conteo (kind='closing') y devuelve,
    por insumo, cuánto se gastó desde la última vez (lo que decían los registros menos lo
    contado). Un gasto negativo es que apareció más de lo que había anotado."""
    efectiva = _branch_for(current_user, data.branch_id, db)
    if efectiva != data.branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes cerrar el turno de otra sucursal.")
    permitidos = {a.inventory_item_id for a in _sheet_settings(db, efectiva)}
    fuera = [l.inventory_item_id for l in data.lines if l.inventory_item_id not in permitidos]
    if fuera:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hay insumos que no están en la hoja de esta sucursal.")

    conteo = create_count(
        StockCountCreate(
            branch_id=efectiva, notes=(data.notes or None),
            items=[StockCountItemCreate(inventory_item_id=l.inventory_item_id, counted_quantity=l.counted_quantity) for l in data.lines],
        ),
        db=db, current_user=current_user,
    )
    record = db.get(StockCount, conteo.id)
    record.kind = COUNT_KIND_CLOSING
    log_audit_event(db, current_user.id, efectiva, "closing_sheet.create", "stock_count", record.id, {"items": len(record.items)})
    db.commit()

    lineas = []
    costo_total = Decimal("0")
    con_costo = False
    for li in record.items:
        gasto = -Decimal(li.difference)
        costo = None
        if li.unit_cost is not None and gasto > 0:
            costo = (gasto * Decimal(li.unit_cost)).quantize(Decimal("0.01"))
            costo_total += costo
            con_costo = True
        lineas.append({
            "inventory_item_id": li.inventory_item_id, "item_name": li.inventory_item.name, "unit": li.inventory_item.unit,
            "counted_quantity": li.counted_quantity, "used": _q3(gasto), "used_cost": costo,
            "stock_after": li.counted_quantity,
        })
    lineas.sort(key=lambda l: (-(l["used_cost"] or 0), -l["used"]))
    logger.info(f"Cierre de turno #{record.id} en sucursal {efectiva} por {current_user.name}: {len(lineas)} insumo(s)")
    return {
        "id": record.id, "branch_id": efectiva, "counted_at": record.counted_at, "is_first_count": conteo.is_first_count,
        "lines": lineas, "used_cost": costo_total if con_costo else None,
    }


@router.get("/history")
def sheet_history(
    branch_id: Optional[int] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Los últimos cierres de la sucursal, con lo gastado en cada uno."""
    efectiva = _branch_for(current_user, branch_id, db)
    cierres = (
        db.query(StockCount).options(joinedload(StockCount.counted_by_user), joinedload(StockCount.items).joinedload(StockCountItem.inventory_item))
        .filter(StockCount.branch_id == efectiva, StockCount.kind == COUNT_KIND_CLOSING)
        .order_by(StockCount.counted_at.desc(), StockCount.id.desc()).limit(limit).all()
    )
    out = []
    for c in cierres:
        lineas = [{
            "inventory_item_id": li.inventory_item_id, "item_name": li.inventory_item.name, "unit": li.inventory_item.unit,
            "counted_quantity": li.counted_quantity, "used": _q3(-Decimal(li.difference)),
        } for li in c.items]
        lineas.sort(key=lambda l: -l["used"])
        out.append({"id": c.id, "at": c.counted_at, "by": c.counted_by_user.name, "notes": c.notes, "lines": lineas})
    return out
