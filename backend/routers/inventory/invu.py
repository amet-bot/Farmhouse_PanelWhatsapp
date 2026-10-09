"""
Inventario · Estado y sincronizaciones manuales con Invu POS.

Parte del paquete routers/inventory (antes un solo archivo de 3 000 líneas). Todos los
endpoints se registran en el mismo `router`, así que las rutas no cambian.
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.inventory_item import InventoryItem
from models.supplier import Supplier
from models.user import User
from schemas.inventory import (
    InvuStatusResponse, InvuSyncResult,
)
from services import invu_client, invu_items_sync, invu_sync
from security.auth import get_current_authorized_user

from .common import logger, router



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
