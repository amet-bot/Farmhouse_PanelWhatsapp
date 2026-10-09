"""
Inventario · Existencias por sucursal.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from decimal import Decimal
from typing import List, Optional

from fastapi import Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from models.user import User
from models.waste import WasteRecord, WasteItem
from models.stock_count import StockCount, StockCountItem
from models.consumption import ConsumptionRecord, ConsumptionItem
from schemas.inventory import (
    StockRowResponse,
)
from security.auth import get_current_authorized_user

from .common import router
from .helpers import _last_costs_map, _transfer_net_map, _vendido_desde_conteo, _visible_branch_filter



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
                WasteItem.quantity * func.coalesce(WasteItem.unit_cost, InventoryItem.effective_cost, 0)
            ), 0).label("costo"),
            func.coalesce(func.sum(
                case((WasteItem.unit_cost.is_(None), WasteItem.quantity * func.coalesce(InventoryItem.effective_cost, 0)), else_=0)
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
    # Lo que el equipo registró como consumido: también sale de la existencia (igual que en
    # `_on_hand_map`, que es la que usa el conteo); sin esto esta pantalla y el conteo no coinciden.
    consumos_q = (
        db.query(ConsumptionItem.inventory_item_id, func.coalesce(func.sum(ConsumptionItem.quantity), 0))
        .join(ConsumptionRecord, ConsumptionRecord.id == ConsumptionItem.consumption_record_id)
    )
    if efectiva is not None:
        consumos_q = consumos_q.filter(ConsumptionRecord.branch_id == efectiva)
    consumos = {iid: Decimal(c) for iid, c in consumos_q.group_by(ConsumptionItem.inventory_item_id).all()}
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
        if only_stocked and not entrada and not salida and not ajuste and item.id not in traslados and item.id not in consumos:
            continue

        entro = Decimal(entrada.cantidad) if entrada else Decimal("0")
        salio = Decimal(salida.cantidad) if salida else Decimal("0")
        ajustado = Decimal(ajuste.cantidad) if ajuste else Decimal("0")
        consumido = consumos.get(item.id, Decimal("0"))
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
            consumed=consumido,
            sold_since_count=(vendido.quantize(Decimal("0.001")) if vendido else None),
            on_hand=entro - salio - consumido + ajustado + trasladado - (vendido or Decimal("0")),
            wasted_cost=(Decimal(salida.costo).quantize(Decimal("0.01")) if salida and salida.costo else None),
            wasted_cost_estimated=bool(salida and salida.costo_estimado and Decimal(salida.costo_estimado) > 0),
            last_movement_at=(max(fechas) if fechas else None),
            last_unit_cost=ultimos_costos.get(item.id),
            last_counted_at=(ajuste.ultimo if ajuste else None),
        ))

    return filas
