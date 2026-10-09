"""
Inventario · Libro de movimientos (comparación con el cálculo histórico).

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""
from decimal import Decimal
from typing import List

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.inventory_item import InventoryItem
from models.user import User
from models.inventory_movement import InventoryMovement
from schemas.inventory import (
    MovementComparisonResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid

from .common import router
from .helpers import _on_hand_map



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
