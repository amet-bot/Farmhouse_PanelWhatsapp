import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.inventory_item import InventoryItem
from models.inventory_movement import InventoryMovement
from models.transfer import Transfer, TransferItem
from models.user import User
from schemas.transfer import (
    TransferActionRequest, TransferCreate, TransferItemResponse, TransferResponse,
)
from security.auth import get_current_authorized_user
from security.access_control import check_target_branch_valid
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.transfers")

router = APIRouter(prefix="/transfers", tags=["Transferencias"])


def _is_branch_staff_or_admin(current_user: User, branch_id: int) -> bool:
    """
    admin y supervisor global pueden actuar sobre cualquier sucursal; el resto (agente y
    supervisor local) solo sobre la suya propia — mismo criterio que el resto del módulo de
    inventario, aplicado acá a la sucursal de origen o destino según la acción.
    """
    if current_user.role == "admin":
        return True
    if current_user.role == "supervisor" and current_user.branch_id is None:
        return True
    return current_user.branch_id == branch_id


def _require_branch_staff_or_admin(current_user: User, branch_id: int, detail: str):
    if not _is_branch_staff_or_admin(current_user, branch_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _serialize(transfer: Transfer) -> TransferResponse:
    return TransferResponse(
        id=transfer.id,
        from_branch_id=transfer.from_branch_id,
        from_branch_name=transfer.from_branch.name,
        to_branch_id=transfer.to_branch_id,
        to_branch_name=transfer.to_branch.name,
        status=transfer.status,
        requested_by_user_id=transfer.requested_by_user_id,
        requested_by_name=transfer.requested_by_user.name,
        approved_by_user_id=transfer.approved_by_user_id,
        dispatched_by_user_id=transfer.dispatched_by_user_id,
        received_by_user_id=transfer.received_by_user_id,
        requested_at=transfer.requested_at,
        approved_at=transfer.approved_at,
        dispatched_at=transfer.dispatched_at,
        received_at=transfer.received_at,
        notes=transfer.notes,
        created_at=transfer.created_at,
        items=[
            TransferItemResponse(
                id=line.id,
                inventory_item_id=line.inventory_item_id,
                item_name=line.inventory_item.name,
                unit=line.inventory_item.unit,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
            )
            for line in transfer.items
        ],
    )


def _claim_transition(db: Session, transfer: Transfer, from_statuses: tuple, to_status: str) -> None:
    """
    Cambia el estado con un UPDATE condicional (… WHERE status IN from_statuses) en vez de
    leer-y-escribir: dos "despachar" (o "recibir") simultáneos pasaban los dos el chequeo de
    estado y cada uno escribía sus movimientos en el libro — el insumo salía o entraba dos veces.
    Ahora solo uno gana; el otro recibe 409.
    """
    updated = db.query(Transfer).filter(
        Transfer.id == transfer.id, Transfer.status.in_(from_statuses)
    ).update({Transfer.status: to_status}, synchronize_session=False)
    if updated != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El traslado cambió de estado mientras tanto. Recargá para ver su estado actual.",
        )
    transfer.status = to_status


def _get_transfer_or_404(db: Session, transfer_id: int) -> Transfer:
    transfer = db.query(Transfer).options(
        joinedload(Transfer.items).joinedload(TransferItem.inventory_item),
        joinedload(Transfer.from_branch),
        joinedload(Transfer.to_branch),
        joinedload(Transfer.requested_by_user),
    ).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transferencia no encontrada.")
    return transfer


@router.post("/", response_model=TransferResponse, status_code=status.HTTP_201_CREATED)
def create_transfer(
    transfer_in: TransferCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Crea un traslado en estado 'requested'. No mueve inventario todavía."""
    if transfer_in.from_branch_id == transfer_in.to_branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La sucursal de origen y destino no pueden ser la misma."
        )

    # Puede pedirlo quien está en cualquiera de las dos puntas (el que manda o el que necesita) —
    # admin y supervisor global, cualquier combinación.
    if not _is_branch_staff_or_admin(current_user, transfer_in.from_branch_id) and \
       not _is_branch_staff_or_admin(current_user, transfer_in.to_branch_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para crear traslados entre esas sucursales."
        )

    check_target_branch_valid(db, transfer_in.from_branch_id)
    check_target_branch_valid(db, transfer_in.to_branch_id)

    item_ids = [line.inventory_item_id for line in transfer_in.items]
    found_items = db.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).all()
    missing = set(item_ids) - {i.id for i in found_items}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ítem(s) de inventario no encontrados: {sorted(missing)}"
        )

    transfer = Transfer(
        from_branch_id=transfer_in.from_branch_id,
        to_branch_id=transfer_in.to_branch_id,
        status="requested",
        requested_by_user_id=current_user.id,
        requested_at=datetime.now(timezone.utc),
        notes=(transfer_in.notes or None),
    )
    for line in transfer_in.items:
        transfer.items.append(TransferItem(
            inventory_item_id=line.inventory_item_id,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
        ))

    db.add(transfer)
    db.flush()  # asigna transfer.id antes de auditar (Fase 4)
    log_audit_event(
        db, current_user.id, current_user.branch_id, "transfer.request", "transfer", transfer.id,
        {"from_branch_id": transfer.from_branch_id, "to_branch_id": transfer.to_branch_id}
    )
    db.commit()
    db.refresh(transfer)
    logger.info(f"Traslado #{transfer.id} solicitado de sucursal {transfer.from_branch_id} a {transfer.to_branch_id} por {current_user.name}")
    return _serialize(transfer)


@router.get("/", response_model=List[TransferResponse])
def list_transfers(
    branch_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Lista traslados donde la sucursal del usuario participa (como origen o destino)."""
    query = db.query(Transfer).options(
        joinedload(Transfer.items).joinedload(TransferItem.inventory_item),
        joinedload(Transfer.from_branch),
        joinedload(Transfer.to_branch),
        joinedload(Transfer.requested_by_user),
    )

    is_global = current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)
    if not is_global:
        query = query.filter(
            (Transfer.from_branch_id == current_user.branch_id) | (Transfer.to_branch_id == current_user.branch_id)
        )
    elif branch_id is not None:
        query = query.filter((Transfer.from_branch_id == branch_id) | (Transfer.to_branch_id == branch_id))

    if status_filter:
        query = query.filter(Transfer.status == status_filter)

    transfers = query.order_by(Transfer.requested_at.desc()).offset(offset).limit(limit).all()
    return [_serialize(t) for t in transfers]


@router.get("/{transfer_id}", response_model=TransferResponse)
def get_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    transfer = _get_transfer_or_404(db, transfer_id)
    if not _is_branch_staff_or_admin(current_user, transfer.from_branch_id) and \
       not _is_branch_staff_or_admin(current_user, transfer.to_branch_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permiso para ver este traslado.")
    return _serialize(transfer)


@router.post("/{transfer_id}/approve", response_model=TransferResponse)
def approve_transfer(
    transfer_id: int,
    action: TransferActionRequest = TransferActionRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """La sucursal de origen confirma que va a mandar el insumo."""
    transfer = _get_transfer_or_404(db, transfer_id)
    _require_branch_staff_or_admin(current_user, transfer.from_branch_id, "Solo la sucursal de origen puede aprobar este traslado.")
    if transfer.status != "requested":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El traslado está en estado '{transfer.status}', no se puede aprobar.")

    _claim_transition(db, transfer, ("requested",), "approved")
    transfer.approved_by_user_id = current_user.id
    transfer.approved_at = datetime.now(timezone.utc)
    if action.notes:
        transfer.notes = f"{transfer.notes}\n{action.notes}" if transfer.notes else action.notes
    log_audit_event(db, current_user.id, transfer.from_branch_id, "transfer.approve", "transfer", transfer.id)
    db.commit()
    db.refresh(transfer)
    return _serialize(transfer)


@router.post("/{transfer_id}/dispatch", response_model=TransferResponse)
def dispatch_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Sale de la sucursal de origen: genera el movimiento transfer_out en el libro (Fase 4)."""
    transfer = _get_transfer_or_404(db, transfer_id)
    _require_branch_staff_or_admin(current_user, transfer.from_branch_id, "Solo la sucursal de origen puede despachar este traslado.")
    if transfer.status != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El traslado está en estado '{transfer.status}', no se puede despachar.")

    now = datetime.now(timezone.utc)
    _claim_transition(db, transfer, ("approved",), "dispatched")
    transfer.dispatched_by_user_id = current_user.id
    transfer.dispatched_at = now
    for line in transfer.items:
        db.add(InventoryMovement(
            branch_id=transfer.from_branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="transfer_out",
            quantity=-line.quantity,
            unit_cost=line.unit_cost,
            occurred_at=now,
            source_type="transfer",
            source_id=transfer.id,
            created_by_user_id=current_user.id,
        ))
    log_audit_event(db, current_user.id, transfer.from_branch_id, "transfer.dispatch", "transfer", transfer.id)
    db.commit()
    db.refresh(transfer)
    logger.info(f"Traslado #{transfer.id} despachado de sucursal {transfer.from_branch_id} por {current_user.name}")
    return _serialize(transfer)


@router.post("/{transfer_id}/receive", response_model=TransferResponse)
def receive_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Entra a la sucursal de destino: genera el movimiento transfer_in en el libro (Fase 4)."""
    transfer = _get_transfer_or_404(db, transfer_id)
    _require_branch_staff_or_admin(current_user, transfer.to_branch_id, "Solo la sucursal de destino puede recibir este traslado.")
    if transfer.status != "dispatched":
        # Cubre tanto "todavía no lo despacharon" como "ya lo recibieron antes" — no se puede
        # recibir dos veces la misma transferencia.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El traslado está en estado '{transfer.status}', no se puede recibir.")

    now = datetime.now(timezone.utc)
    _claim_transition(db, transfer, ("dispatched",), "received")
    transfer.received_by_user_id = current_user.id
    transfer.received_at = now
    for line in transfer.items:
        db.add(InventoryMovement(
            branch_id=transfer.to_branch_id,
            inventory_item_id=line.inventory_item_id,
            movement_type="transfer_in",
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            occurred_at=now,
            source_type="transfer",
            source_id=transfer.id,
            created_by_user_id=current_user.id,
        ))
    log_audit_event(db, current_user.id, transfer.to_branch_id, "transfer.receive", "transfer", transfer.id)
    db.commit()
    db.refresh(transfer)
    logger.info(f"Traslado #{transfer.id} recibido en sucursal {transfer.to_branch_id} por {current_user.name}")
    return _serialize(transfer)


@router.post("/{transfer_id}/reject", response_model=TransferResponse)
def reject_transfer(
    transfer_id: int,
    action: TransferActionRequest = TransferActionRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """La sucursal de origen rechaza el traslado antes de despacharlo — nunca salió inventario."""
    transfer = _get_transfer_or_404(db, transfer_id)
    _require_branch_staff_or_admin(current_user, transfer.from_branch_id, "Solo la sucursal de origen puede rechazar este traslado.")
    if transfer.status not in ("requested", "approved"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El traslado está en estado '{transfer.status}', ya no se puede rechazar.")

    _claim_transition(db, transfer, ("requested", "approved"), "rejected")
    if action.notes:
        transfer.notes = f"{transfer.notes}\n{action.notes}" if transfer.notes else action.notes
    log_audit_event(db, current_user.id, transfer.from_branch_id, "transfer.reject", "transfer", transfer.id)
    db.commit()
    db.refresh(transfer)
    return _serialize(transfer)


@router.post("/{transfer_id}/cancel", response_model=TransferResponse)
def cancel_transfer(
    transfer_id: int,
    action: TransferActionRequest = TransferActionRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Quien lo pidió (o quien administra) lo cancela antes de que salga inventario."""
    transfer = _get_transfer_or_404(db, transfer_id)
    is_requester = current_user.id == transfer.requested_by_user_id
    is_admin_like = current_user.role == "admin" or (current_user.role == "supervisor" and current_user.branch_id is None)
    if not (is_requester or is_admin_like):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permiso para cancelar este traslado.")
    if transfer.status not in ("requested", "approved"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El traslado está en estado '{transfer.status}', ya no se puede cancelar.")

    _claim_transition(db, transfer, ("requested", "approved"), "cancelled")
    if action.notes:
        transfer.notes = f"{transfer.notes}\n{action.notes}" if transfer.notes else action.notes
    log_audit_event(db, current_user.id, transfer.from_branch_id, "transfer.cancel", "transfer", transfer.id)
    db.commit()
    db.refresh(transfer)
    return _serialize(transfer)
