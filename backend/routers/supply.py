"""
Abastecimiento (bloque 3): existencias por sucursal (cuánto queda y para cuántos días),
mínimos y pares por insumo y sucursal, pedido sugerido por proveedor según el ritmo de uso,
órdenes de compra (cargamentos esperados con cantidades), lista de precios por proveedor a
partir del historial de compras y stock bajo.

Acceso: ver es de cualquier persona de la sucursal (o global); editar mínimos, crear órdenes y
todo lo que compromete plata es de encargados (inventory.adjust).
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.branch import Branch
from models.consumption import ConsumptionItem, ConsumptionRecord
from models.inventory_item import InventoryItem
from models.ops import SupplyRequest
from models.shipment import ExpectedShipment, Shipment, ShipmentItem
from models.stock_count import StockCount, StockCountItem
from models.supplier import Supplier
from models.supply import ExpectedShipmentItem, ItemBranchSetting
from models.user import User
from models.waste import WasteItem, WasteRecord
from routers.inventory import _existencia_map, _last_known_cost, _recetas_de_sucursal, _uso_por_ventas, _visible_branch_filter
from routers.receiving import _avisar_agendado_background, _serialize_expected
from security.access_control import check_target_branch_valid
from security.auth import get_current_authorized_user
from security.permissions import require_permission
from services.audit import log_audit_event
from services.invu_sales_sync import hoy_panama
from services.supply_alerts import low_stock_rows

logger = logging.getLogger("farmhouse.supply")

router = APIRouter(prefix="/supply", tags=["Abastecimiento"])

USAGE_DAYS = 14          # ventana para el ritmo de uso
DEFAULT_LEAD_DAYS = 2    # si el insumo no tiene días de entrega cargados


def _q3(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.001"))


def _q2(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _branch_or_403(db: Session, current_user: User, branch_id: Optional[int]) -> Branch:
    efectiva = _visible_branch_filter(current_user, branch_id)
    if efectiva is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Indica la sucursal.")
    if branch_id is not None and efectiva != branch_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permiso sobre esa sucursal.")
    return check_target_branch_valid(db, efectiva)


# ==========================================================================
# Ritmo de uso
# ==========================================================================
def usage_per_day(db: Session, branch_id: int, item_ids: List[int], days: int = USAGE_DAYS) -> dict:
    """Cuánto sale por día de cada insumo en esa sucursal, según los últimos `days` días: el
    consumo registrado a mano; si no hay, lo que revelaron las hojas de cierre (conteos
    kind='closing': lo que faltó entre un cierre y el siguiente es lo que se gastó); y si tampoco,
    el estimado por ventas × recetas. Más la merma en todos los casos.
    Mismo criterio que la existencia: lo registrado a mano manda sobre lo estimado."""
    if not item_ids:
        return {}
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    desde = ahora - timedelta(days=days)
    manual = dict(
        db.query(ConsumptionItem.inventory_item_id, func.coalesce(func.sum(ConsumptionItem.quantity), 0))
        .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
        .filter(ConsumptionRecord.branch_id == branch_id, ConsumptionRecord.occurred_at >= desde, ConsumptionItem.inventory_item_id.in_(item_ids))
        .group_by(ConsumptionItem.inventory_item_id).all()
    )
    cierres: dict = {}
    for iid, dif in (
        db.query(StockCountItem.inventory_item_id, StockCountItem.difference)
        .join(StockCount, StockCount.id == StockCountItem.stock_count_id)
        .filter(StockCount.branch_id == branch_id, StockCount.kind == "closing", StockCount.counted_at >= desde, StockCountItem.inventory_item_id.in_(item_ids))
        .all()
    ):
        if dif is not None and Decimal(dif) < 0:
            cierres[iid] = cierres.get(iid, Decimal("0")) - Decimal(dif)
    merma = dict(
        db.query(WasteItem.inventory_item_id, func.coalesce(func.sum(WasteItem.quantity), 0))
        .join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id)
        .filter(WasteRecord.branch_id == branch_id, WasteRecord.occurred_at >= desde, WasteItem.inventory_item_id.in_(item_ids))
        .group_by(WasteItem.inventory_item_id).all()
    )
    try:
        teorico = _uso_por_ventas(db, branch_id, desde, ahora, recetas_insumos=_recetas_de_sucursal(db, branch_id))
    except Exception:
        logger.warning("[Supply] No se pudo estimar el uso por ventas de la sucursal %s.", branch_id, exc_info=True)
        teorico = {}
    rate = {}
    for iid in item_ids:
        if iid in manual:
            base = Decimal(manual[iid])
        elif iid in cierres:
            base = cierres[iid]
        else:
            base = Decimal(teorico.get(iid, 0) or 0)
        total = base + Decimal(merma.get(iid, 0) or 0)
        if total > 0:
            rate[iid] = total / Decimal(days)
    return rate


def _in_transit_map(db: Session, branch_id: int) -> dict:
    """Lo que ya viene en órdenes de compra pendientes, por insumo."""
    return dict(
        db.query(ExpectedShipmentItem.inventory_item_id, func.coalesce(func.sum(ExpectedShipmentItem.quantity), 0))
        .join(ExpectedShipment, ExpectedShipment.id == ExpectedShipmentItem.expected_shipment_id)
        .filter(ExpectedShipment.branch_id == branch_id, ExpectedShipment.status == "pendiente")
        .group_by(ExpectedShipmentItem.inventory_item_id).all()
    )


def _open_requests_map(db: Session, branch_id: int) -> dict:
    """Solicitudes de insumos abiertas o aprobadas ligadas a un insumo del catálogo, por insumo."""
    return dict(
        db.query(SupplyRequest.inventory_item_id, func.coalesce(func.sum(SupplyRequest.quantity), 0))
        .filter(SupplyRequest.branch_id == branch_id, SupplyRequest.status.in_(["open", "approved"]), SupplyRequest.inventory_item_id.isnot(None))
        .group_by(SupplyRequest.inventory_item_id).all()
    )


def _last_supplier_map(db: Session, branch_id: int) -> dict:
    """El último proveedor que trajo cada insumo a esa sucursal."""
    filas = (
        db.query(ShipmentItem.inventory_item_id, Shipment.supplier_id)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .filter(Shipment.branch_id == branch_id, Shipment.supplier_id.isnot(None))
        .order_by(Shipment.received_at.asc(), ShipmentItem.id.asc()).all()
    )
    return {iid: sid for iid, sid in filas}


# ==========================================================================
# Cuánto le queda a cada sucursal
# ==========================================================================
def _branches_with_data(db: Session, branch_ids: List[int]) -> set:
    """Sucursales que ya tienen algún cargamento o conteo: sin eso, la existencia es 0 en todo y
    no significa que no haya nada, sino que todavía no se cargó el arranque."""
    con = set(b for (b,) in db.query(Shipment.branch_id).filter(Shipment.branch_id.in_(branch_ids)).distinct().all())
    con |= set(b for (b,) in db.query(StockCount.branch_id).filter(StockCount.branch_id.in_(branch_ids)).distinct().all())
    return con


@router.get("/stock")
def stock_by_branch(
    branch_id: Optional[int] = Query(None),
    q: str = Query("", max_length=150),
    category: str = Query("", max_length=100),
    only_low: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Listado de cuánto le queda a cada sucursal de cada insumo: una fila por insumo, una columna
    por sucursal (las que el usuario ve), con existencia, para cuántos días alcanza y si está
    bajo el mínimo. Las sucursales sin cargamentos ni conteos vienen marcadas `has_data: false`
    para que la pantalla diga "sin datos" en vez de mostrar ceros como si fueran reales.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)
    bq = db.query(Branch).filter(Branch.active == True, Branch.code != "CAT")  # noqa: E712
    if efectiva is not None:
        bq = bq.filter(Branch.id == efectiva)
    branches = bq.order_by(Branch.name).all()
    bids = [b.id for b in branches]

    iq = db.query(InventoryItem).filter(InventoryItem.active == True)  # noqa: E712
    if q.strip():
        iq = iq.filter(InventoryItem.name.ilike(f"%{q.strip()}%"))
    if category.strip():
        iq = iq.filter(InventoryItem.category == category.strip())
    items = iq.order_by(InventoryItem.category, InventoryItem.name).all()
    ids = [i.id for i in items]

    con_datos = _branches_with_data(db, bids) if bids else set()
    settings = defaultdict(dict)
    for s in db.query(ItemBranchSetting).filter(ItemBranchSetting.branch_id.in_(bids)).all() if bids else []:
        settings[s.branch_id][s.inventory_item_id] = s
    por_sucursal = {}
    for b in branches:
        stock = _existencia_map(db, b.id, ids) if (ids and b.id in con_datos) else {}
        rate = usage_per_day(db, b.id, ids) if (ids and b.id in con_datos) else {}
        por_sucursal[b.id] = (stock, rate)

    rows = []
    low_total = 0
    for i in items:
        celdas = {}
        alguna_baja = False
        for b in branches:
            stock, rate = por_sucursal[b.id]
            s = settings[b.id].get(i.id)
            if b.id not in con_datos:
                celdas[str(b.id)] = None
                continue
            st = Decimal(stock.get(i.id, Decimal("0")))
            r = rate.get(i.id)
            baja = bool(s and s.min_quantity is not None and st < Decimal(s.min_quantity))
            alguna_baja = alguna_baja or baja
            celdas[str(b.id)] = {
                "stock": _q3(st),
                "days_left": _q2(st / r) if r and st > 0 else (Decimal("0") if r else None),
                "min_quantity": s.min_quantity if s else None,
                "below_min": baja,
            }
        if only_low and not alguna_baja:
            continue
        if alguna_baja:
            low_total += 1
        rows.append({"inventory_item_id": i.id, "name": i.name, "unit": i.unit, "category": i.category, "below_min_anywhere": alguna_baja, "cells": celdas})

    categorias = sorted({i.category for i in db.query(InventoryItem).filter(InventoryItem.active == True).all() if i.category})  # noqa: E712
    return {
        "branches": [{"id": b.id, "name": b.name, "code": b.code, "has_data": b.id in con_datos} for b in branches],
        "rows": rows, "categories": categorias, "items_below_min": low_total, "usage_days": USAGE_DAYS,
    }


# ==========================================================================
# Existencias y mínimos por sucursal
# ==========================================================================
class SettingIn(BaseModel):
    inventory_item_id: int
    branch_id: int
    min_quantity: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=3)
    par_quantity: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=3)
    supplier_id: Optional[int] = None
    lead_days: Optional[int] = Field(None, ge=0, le=60)


@router.get("/settings")
def list_settings(
    branch_id: Optional[int] = Query(None),
    q: str = Query("", max_length=150),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Todos los insumos activos de la sucursal con lo que queda, el ritmo de uso por día, para
    cuántos días alcanza, y su mínimo, par, proveedor preferido y días de entrega. Es a la vez
    el listado de existencias por sucursal y la pantalla de mínimos y pares."""
    branch = _branch_or_403(db, current_user, branch_id)
    query = db.query(InventoryItem).filter(InventoryItem.active == True)  # noqa: E712
    if q.strip():
        query = query.filter(InventoryItem.name.ilike(f"%{q.strip()}%") | InventoryItem.category.ilike(f"%{q.strip()}%"))
    items = query.order_by(InventoryItem.name).all()
    ids = [i.id for i in items]
    settings = {s.inventory_item_id: s for s in db.query(ItemBranchSetting).options(joinedload(ItemBranchSetting.supplier)).filter(ItemBranchSetting.branch_id == branch.id).all()}
    stock = _existencia_map(db, branch.id, ids) if ids else {}
    rate = usage_per_day(db, branch.id, ids) if ids else {}
    en_camino = _in_transit_map(db, branch.id)
    rows = []
    for i in items:
        s = settings.get(i.id)
        r = rate.get(i.id)
        st = Decimal(stock.get(i.id, Decimal("0")))
        rows.append({
            "inventory_item_id": i.id, "name": i.name, "unit": i.unit, "category": i.category,
            "stock": _q3(st), "usage_per_day": _q3(r) if r else None,
            "days_left": _q2(st / r) if r and st > 0 else (Decimal("0") if r else None),
            "in_transit": _q3(en_camino.get(i.id, 0) or 0),
            "min_quantity": s.min_quantity if s else None, "par_quantity": s.par_quantity if s else None,
            "supplier_id": s.supplier_id if s else None, "supplier_name": s.supplier.name if s and s.supplier else None,
            "lead_days": s.lead_days if s else None,
            "below_min": bool(s and s.min_quantity is not None and st < Decimal(s.min_quantity)),
        })
    return {"branch_id": branch.id, "branch_name": branch.name, "usage_days": USAGE_DAYS, "rows": rows}


@router.put("/settings")
def save_settings(
    data: List[SettingIn],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Guarda mínimos, pares, proveedor preferido y días de entrega (varios de una vez)."""
    if not data:
        return {"saved": 0}
    guardados = 0
    for s in data:
        _branch_or_403(db, current_user, s.branch_id)
        if not db.query(InventoryItem.id).filter(InventoryItem.id == s.inventory_item_id).first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Insumo {s.inventory_item_id} no encontrado.")
        if s.supplier_id is not None and not db.query(Supplier.id).filter(Supplier.id == s.supplier_id).first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El proveedor no existe.")
        if s.min_quantity is not None and s.par_quantity is not None and s.par_quantity < s.min_quantity:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El par no puede ser menor que el mínimo.")
        row = db.query(ItemBranchSetting).filter(ItemBranchSetting.inventory_item_id == s.inventory_item_id, ItemBranchSetting.branch_id == s.branch_id).first()
        if row is None:
            row = ItemBranchSetting(inventory_item_id=s.inventory_item_id, branch_id=s.branch_id)
            db.add(row)
        antes = {"min": str(row.min_quantity), "par": str(row.par_quantity), "supplier_id": row.supplier_id, "lead_days": row.lead_days}
        row.min_quantity = s.min_quantity
        row.par_quantity = s.par_quantity
        row.supplier_id = s.supplier_id
        row.lead_days = s.lead_days
        row.updated_by_user_id = current_user.id
        db.flush()
        log_audit_event(db, current_user.id, s.branch_id, "supply_setting.save", "inventory_item", s.inventory_item_id,
                        {"before": antes, "after": {"min": str(s.min_quantity), "par": str(s.par_quantity), "supplier_id": s.supplier_id, "lead_days": s.lead_days}})
        guardados += 1
    db.commit()
    return {"saved": guardados}


# ==========================================================================
# Pedido sugerido
# ==========================================================================
@router.get("/suggested")
def suggested_order(
    branch_id: Optional[int] = Query(None),
    cover_days: int = Query(7, ge=1, le=60),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Qué pedir y a quién, por sucursal. Para cada insumo: existencia, ritmo de uso, días que
    alcanza, lo que ya viene en órdenes pendientes y las solicitudes abiertas del equipo.
    Se sugiere pedir cuando está bajo el mínimo, cuando no alcanza para cubrir `cover_days`
    más los días de entrega, o cuando el equipo lo pidió. Cantidad: hasta el par si lo tiene;
    si no, lo que cubra esos días; menos lo que ya viene. Agrupado por proveedor preferido (o
    el último que lo trajo).
    """
    branch = _branch_or_403(db, current_user, branch_id)
    items = db.query(InventoryItem).filter(InventoryItem.active == True).order_by(InventoryItem.name).all()  # noqa: E712
    ids = [i.id for i in items]
    settings = {s.inventory_item_id: s for s in db.query(ItemBranchSetting).filter(ItemBranchSetting.branch_id == branch.id).all()}
    stock = _existencia_map(db, branch.id, ids) if ids else {}
    rate = usage_per_day(db, branch.id, ids) if ids else {}
    en_camino = _in_transit_map(db, branch.id)
    pedidos = _open_requests_map(db, branch.id)
    ultimo_prov = _last_supplier_map(db, branch.id)
    proveedores = {p.id: p.name for p in db.query(Supplier).all()}

    grupos: dict = defaultdict(list)
    total_lineas = 0
    total_costo = Decimal("0")
    for i in items:
        s = settings.get(i.id)
        st = Decimal(stock.get(i.id, Decimal("0")))
        r = rate.get(i.id)
        lead = (s.lead_days if s and s.lead_days is not None else DEFAULT_LEAD_DAYS)
        min_q = Decimal(s.min_quantity) if s and s.min_quantity is not None else None
        par = Decimal(s.par_quantity) if s and s.par_quantity is not None else None
        viene = Decimal(en_camino.get(i.id, 0) or 0)
        pedido = Decimal(pedidos.get(i.id, 0) or 0)
        days_left = (st / r) if r and st > 0 else (Decimal("0") if r else None)

        motivos = []
        if min_q is not None and st < min_q:
            motivos.append("bajo el mínimo")
        if r and (days_left is None or days_left < cover_days + lead):
            motivos.append(f"alcanza {days_left:.1f} días" if days_left else "sin existencia")
        if pedido > 0:
            motivos.append("lo pidió el equipo")
        if not motivos:
            continue

        if par is not None:
            objetivo = par
        elif r:
            objetivo = r * Decimal(cover_days + lead)
        else:
            objetivo = min_q or Decimal("0")
        sugerido = max(Decimal("0"), objetivo - st - viene)
        if pedido > 0:
            sugerido = max(sugerido, pedido - viene)
        if sugerido <= 0 and "bajo el mínimo" not in motivos:
            continue
        sugerido = _q3(sugerido)
        costo = _last_known_cost(db, branch.id, i.id)
        if costo is None:
            costo = i.reference_cost
        sid = (s.supplier_id if s and s.supplier_id else ultimo_prov.get(i.id))
        linea = {
            "inventory_item_id": i.id, "name": i.name, "unit": i.unit, "category": i.category,
            "stock": _q3(st), "usage_per_day": _q3(r) if r else None, "days_left": _q2(days_left) if days_left is not None else None,
            "min_quantity": min_q, "par_quantity": par, "lead_days": lead, "in_transit": _q3(viene), "requested": _q3(pedido),
            "suggested_qty": sugerido, "unit_cost": costo, "est_cost": _q2(sugerido * Decimal(costo)) if costo is not None else None,
            "reasons": motivos,
        }
        grupos[sid].append(linea)
        total_lineas += 1
        if linea["est_cost"]:
            total_costo += linea["est_cost"]

    salida = []
    for sid, lineas in grupos.items():
        lineas.sort(key=lambda l: (0 if "bajo el mínimo" in l["reasons"] else 1, l["name"]))
        salida.append({
            "supplier_id": sid, "supplier_name": proveedores.get(sid, "Sin proveedor asignado") if sid else "Sin proveedor asignado",
            "lines": lineas, "est_cost": sum((l["est_cost"] or Decimal("0") for l in lineas), Decimal("0")),
        })
    salida.sort(key=lambda g: (g["supplier_id"] is None, -len(g["lines"])))
    return {"branch_id": branch.id, "branch_name": branch.name, "cover_days": cover_days, "usage_days": USAGE_DAYS,
            "groups": salida, "lines": total_lineas, "est_cost": total_costo}


@router.get("/low-stock")
def low_stock(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    efectiva = _visible_branch_filter(current_user, branch_id)
    branches = db.query(Branch).filter(Branch.active == True)  # noqa: E712
    if efectiva is not None:
        branches = branches.filter(Branch.id == efectiva)
    salida = []
    for b in branches.order_by(Branch.name).all():
        if b.code == "CAT":
            continue
        rows = low_stock_rows(db, b.id)
        salida.append({"branch_id": b.id, "branch_name": b.name, "rows": rows})
    return {"branches": salida, "total": sum(len(b["rows"]) for b in salida)}


# ==========================================================================
# Órdenes de compra (cargamentos esperados con líneas)
# ==========================================================================
class OrderLineIn(BaseModel):
    inventory_item_id: int
    quantity: Decimal = Field(..., gt=0, max_digits=10, decimal_places=3)
    unit_cost: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=4)


class OrderCreate(BaseModel):
    branch_id: int
    supplier_id: Optional[int] = None
    expected_date: date
    time_from: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notes: Optional[str] = Field(None, max_length=500)
    items: List[OrderLineIn] = Field(..., min_length=1, max_length=200)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Crea la orden: un cargamento esperado con sus líneas. Avisa a la sucursal como cualquier
    cargamento agendado; al recibir, la recepción arranca con estas líneas como facturado."""
    branch = _branch_or_403(db, current_user, data.branch_id)
    proveedor = None
    if data.supplier_id is not None:
        proveedor = db.query(Supplier).filter(Supplier.id == data.supplier_id).first()
        if not proveedor:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El proveedor seleccionado no existe.")
    if data.expected_date < hoy_panama():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La fecha ya pasó.")
    ids = [l.inventory_item_id for l in data.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hay un insumo repetido en la orden.")
    encontrados = {i.id for i in db.query(InventoryItem.id).filter(InventoryItem.id.in_(ids)).all()}
    if set(ids) - encontrados:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ítem(s) de inventario no encontrados: {sorted(set(ids) - encontrados)}")

    e = ExpectedShipment(
        branch_id=branch.id, supplier_id=data.supplier_id, expected_date=data.expected_date, time_from=data.time_from,
        notes=((data.notes or "").strip() or None), status="pendiente", created_by_user_id=current_user.id,
    )
    for l in data.items:
        costo = l.unit_cost if l.unit_cost is not None else _last_known_cost(db, branch.id, l.inventory_item_id)
        e.items.append(ExpectedShipmentItem(inventory_item_id=l.inventory_item_id, quantity=l.quantity, unit_cost=costo))
    db.add(e)
    db.flush()
    log_audit_event(db, current_user.id, branch.id, "purchase_order.create", "expected_shipment", e.id,
                    {"supplier_id": e.supplier_id, "expected_date": e.expected_date.isoformat(), "lines": len(data.items)})
    db.commit()
    db.refresh(e)
    hoy = hoy_panama()
    cuando = "Hoy" if e.expected_date == hoy else ("Mañana" if e.expected_date == hoy + timedelta(days=1) else f"El {e.expected_date.strftime('%d/%m')}")
    quien = proveedor.name if proveedor else "un pedido"
    background_tasks.add_task(_avisar_agendado_background, e.branch_id, f"{cuando} llega {quien}",
                              f"{branch.name} · orden con {len(data.items)} insumo{'s' if len(data.items) != 1 else ''}. Revisen contra factura.",
                              f"fh-expected-{e.id}")
    return _serialize_expected(e)


@router.get("/orders")
def list_orders(
    branch_id: Optional[int] = Query(None),
    status_filter: str = Query("pendiente", alias="status", pattern="^(pendiente|recibido|cancelado|todos)$"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Órdenes de compra (cargamentos esperados) con sus líneas y costo estimado."""
    efectiva = _visible_branch_filter(current_user, branch_id)
    q = db.query(ExpectedShipment).options(
        joinedload(ExpectedShipment.items).joinedload(ExpectedShipmentItem.inventory_item),
        joinedload(ExpectedShipment.branch), joinedload(ExpectedShipment.supplier), joinedload(ExpectedShipment.created_by_user),
    )
    if efectiva is not None:
        q = q.filter(ExpectedShipment.branch_id == efectiva)
    if status_filter != "todos":
        q = q.filter(ExpectedShipment.status == status_filter)
    return [_serialize_expected(e) for e in q.order_by(ExpectedShipment.expected_date.desc(), ExpectedShipment.id.desc()).limit(limit).all()]


# ==========================================================================
# Precios por proveedor
# ==========================================================================
@router.get("/prices")
def supplier_prices(
    days: int = Query(180, ge=7, le=730),
    supplier_id: Optional[int] = Query(None),
    q: str = Query("", max_length=150),
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Lo que cada proveedor cobró por cada insumo en los últimos `days` días (de todas las
    sucursales que el usuario ve): último precio y fecha, promedio, mínimo y máximo, cuántas
    compras. Por insumo, cuál proveedor tuvo el mejor último precio."""
    efectiva = _visible_branch_filter(current_user, branch_id)
    desde = datetime.utcnow() - timedelta(days=days)
    query = db.query(ShipmentItem).join(Shipment, Shipment.id == ShipmentItem.shipment_id).options(
        joinedload(ShipmentItem.inventory_item), joinedload(ShipmentItem.shipment).joinedload(Shipment.supplier),
    ).filter(Shipment.received_at >= desde, Shipment.supplier_id.isnot(None), ShipmentItem.unit_cost.isnot(None))
    if efectiva is not None:
        query = query.filter(Shipment.branch_id == efectiva)
    if supplier_id is not None:
        query = query.filter(Shipment.supplier_id == supplier_id)
    lines = query.order_by(Shipment.received_at.asc()).all()

    filas: dict = {}
    for li in lines:
        item = li.inventory_item
        if not item or (q.strip() and q.strip().lower() not in f"{item.name} {item.category or ''}".lower()):
            continue
        key = (li.shipment.supplier_id, item.id)
        r = filas.setdefault(key, {
            "supplier_id": li.shipment.supplier_id, "supplier_name": li.shipment.supplier.name if li.shipment.supplier else "",
            "inventory_item_id": item.id, "name": item.name, "unit": item.unit, "category": item.category,
            "purchases": 0, "quantity": Decimal("0"), "amount": Decimal("0"),
            "last_cost": None, "last_date": None, "min_cost": None, "max_cost": None,
        })
        costo = Decimal(li.unit_cost)
        r["purchases"] += 1
        r["quantity"] += Decimal(li.quantity)
        r["amount"] += Decimal(li.quantity) * costo
        r["last_cost"], r["last_date"] = costo, li.shipment.received_at   # ordenado de viejo a nuevo
        r["min_cost"] = costo if r["min_cost"] is None else min(r["min_cost"], costo)
        r["max_cost"] = costo if r["max_cost"] is None else max(r["max_cost"], costo)
    rows = []
    for r in filas.values():
        r["avg_cost"] = (r["amount"] / r["quantity"]).quantize(Decimal("0.0001")) if r["quantity"] else None
        r["quantity"] = _q3(r["quantity"])
        r["amount"] = _q2(r["amount"])
        rows.append(r)
    # Mejor último precio por insumo (cuando lo trae más de un proveedor).
    por_item: dict = defaultdict(list)
    for r in rows:
        por_item[r["inventory_item_id"]].append(r)
    for iid, lst in por_item.items():
        mejor = min(lst, key=lambda r: r["last_cost"])
        for r in lst:
            r["best_price"] = r is mejor
            r["suppliers_for_item"] = len(lst)
            r["vs_best_pct"] = round((float(r["last_cost"]) - float(mejor["last_cost"])) / float(mejor["last_cost"]) * 100, 1) if mejor["last_cost"] and r is not mejor else 0.0
    rows.sort(key=lambda r: (r["name"], r["last_cost"]))
    proveedores = sorted({(r["supplier_id"], r["supplier_name"]) for r in rows}, key=lambda t: t[1])
    return {"days": days, "rows": rows, "suppliers": [{"id": i, "name": n} for i, n in proveedores]}


# ==========================================================================
# Puesta en marcha por sucursal
# ==========================================================================
SETUP_RECENT_DAYS = 7


@router.get("/setup")
def branch_setup(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Qué le falta a cada sucursal para que el inventario sea real: primer conteo completo (el
    arranque), hoja de cierre armada, insumos de la hoja que se cuentan en piezas sin tamaño de
    pieza, mínimos cargados, y si se está cerrando turno (cierres de la última semana).
    Cada paso dice si está hecho; `progress` es cuántos de los cuatro principales.
    """
    from models.inventory_item import InventoryItem as _Item  # local: evita ciclo de imports
    from routers.inventory import _familia_de_unidad

    efectiva = _visible_branch_filter(current_user, None)
    bq = db.query(Branch).filter(Branch.active == True, Branch.code != "CAT")  # noqa: E712
    if efectiva is not None:
        bq = bq.filter(Branch.id == efectiva)
    branches = bq.order_by(Branch.name).all()
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=SETUP_RECENT_DAYS)

    filas = []
    for b in branches:
        conteos = db.query(StockCount.id, StockCount.counted_at, StockCount.kind).filter(StockCount.branch_id == b.id).order_by(StockCount.counted_at.asc()).all()
        primero = conteos[0] if conteos else None
        items_contados = 0
        if primero:
            from models.stock_count import StockCountItem as _SCI
            items_contados = db.query(func.count(_SCI.id)).filter(_SCI.stock_count_id == primero.id).scalar() or 0
        cierres_semana = sum(1 for c in conteos if c.kind == "closing" and c.counted_at >= desde)
        ultimo_cierre = max((c.counted_at for c in conteos if c.kind == "closing"), default=None)

        hoja = (
            db.query(ItemBranchSetting).join(_Item, _Item.id == ItemBranchSetting.inventory_item_id)
            .filter(ItemBranchSetting.branch_id == b.id, ItemBranchSetting.on_closing_sheet == True, _Item.active == True)  # noqa: E712
            .all()
        )
        sin_pieza = [
            s.inventory_item.name for s in hoja
            if s.inventory_item.piece_size is None and _familia_de_unidad(s.inventory_item.unit)[0] != "unidad"
        ]
        con_minimo = db.query(func.count(ItemBranchSetting.id)).filter(
            ItemBranchSetting.branch_id == b.id, ItemBranchSetting.min_quantity.isnot(None)
        ).scalar() or 0

        pasos = {
            "first_count": {"done": primero is not None, "at": primero.counted_at if primero else None, "items": items_contados},
            "closing_sheet": {"done": len(hoja) > 0, "items": len(hoja), "missing_piece_size": sorted(sin_pieza)[:20], "missing_piece_size_count": len(sin_pieza)},
            "minimums": {"done": con_minimo > 0, "items": con_minimo},
            "closings": {"done": cierres_semana > 0, "last_week": cierres_semana, "last_at": ultimo_cierre},
        }
        filas.append({
            "branch": {"id": b.id, "name": b.name, "code": b.code},
            "steps": pasos,
            "progress": sum(1 for p in pasos.values() if p["done"]),
            "total": len(pasos),
        })
    return {"branches": filas, "recent_days": SETUP_RECENT_DAYS}
