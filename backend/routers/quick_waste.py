"""
Merma rápida (/merma): una pantalla aparte, hecha para la tablet de la cocina. Se toca el
insumo (con su foto), se pone cuánto pesa, el motivo, y listo. Registrar usa el mismo
POST /inventory/waste de siempre; aquí solo está lo que la pantalla necesita para ser rápida:
la lista de insumos ordenada por lo que más se bota en esa sucursal y las fotos de los insumos.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.branch import Branch
from models.inventory_item import InventoryItem, ItemPhoto
from models.user import User
from models.waste import WasteItem, WasteRecord
from routers.inventory import WASTE_REASONS, _familia_de_unidad, _image_type, _visible_branch_filter
from security.access_control import check_target_branch_valid
from security.auth import get_current_authorized_user
from security.permissions import has_permission, require_permission
from services.audit import log_audit_event

router = APIRouter(prefix="/quick-waste", tags=["Merma rápida"])

FRECUENTES_DIAS = 60
ITEM_PHOTO_MAX_BYTES = 4 * 1024 * 1024
# Los motivos que se ven como botones grandes, en este orden. El resto queda en "Otro".
MOTIVOS_RAPIDOS = ("vencido", "danado", "error_preparacion", "derrame", "recorte", "otro")


def _es_global(user: User) -> bool:
    return user.role == "admin" or (user.role == "supervisor" and not user.branch_id)


@router.get("/items")
def quick_items(
    branch_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """
    Todo lo que la pantalla necesita de una vez: la sucursal, los insumos activos (con foto si
    tienen, cómo se miden y cuántas veces se botaron ahí en los últimos 60 días, los más botados
    primero) y los motivos. Quien ve todas las sucursales recibe también la lista para elegir.
    """
    efectiva = _visible_branch_filter(current_user, branch_id)
    sucursales = []
    if _es_global(current_user):
        sucursales = [{"id": b.id, "name": b.name} for b in db.query(Branch).filter(Branch.active == True).order_by(Branch.name)]  # noqa: E712
        if efectiva is None and sucursales:
            efectiva = sucursales[0]["id"]
    if efectiva is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Indica la sucursal.")
    check_target_branch_valid(db, efectiva)
    sucursal = db.query(Branch).filter(Branch.id == efectiva).first()

    desde = datetime.now(timezone.utc) - timedelta(days=FRECUENTES_DIAS)
    veces = dict(
        db.query(WasteItem.inventory_item_id, func.count(WasteItem.id))
        .join(WasteRecord, WasteRecord.id == WasteItem.waste_record_id)
        .filter(WasteRecord.branch_id == efectiva, WasteRecord.occurred_at >= desde)
        .group_by(WasteItem.inventory_item_id).all()
    )
    fotos = dict(db.query(ItemPhoto.inventory_item_id, ItemPhoto.updated_at).all())
    items = []
    for it in db.query(InventoryItem).filter(InventoryItem.active == True).all():  # noqa: E712
        familia, base = _familia_de_unidad(it.unit)
        foto = fotos.get(it.id)
        items.append({
            "id": it.id, "name": it.name, "unit": it.unit, "category": it.category,
            "family": familia, "unit_base": base, "piece_size": it.piece_size,
            "times": veces.get(it.id, 0),
            "photo_v": int(foto.replace(tzinfo=foto.tzinfo or timezone.utc).timestamp()) if foto else None,
        })
    items.sort(key=lambda i: (-i["times"], i["name"].lower()))
    etiquetas = dict(WASTE_REASONS)
    return {
        "branch": {"id": sucursal.id, "name": sucursal.name},
        "branches": sucursales,
        "can_edit_photos": has_permission(current_user, "inventory.adjust"),
        "reasons": [{"code": c, "label": etiquetas[c]} for c in MOTIVOS_RAPIDOS if c in etiquetas],
        "items": items,
    }


@router.get("/items/{item_id}/photo")
def item_photo(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    foto = db.query(ItemPhoto).filter(ItemPhoto.inventory_item_id == item_id).first()
    if not foto:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Este insumo no tiene foto.")
    # La URL lleva ?v=<fecha de la foto>: si cambia la foto cambia la URL, así se puede guardar mucho.
    return Response(content=foto.data, media_type=foto.content_type, headers={"Cache-Control": "private, max-age=2592000"})


@router.put("/items/{item_id}/photo")
async def set_item_photo(
    item_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    """Pone (o cambia) la foto de un insumo. Encargado o admin. Solo JPG, PNG o WebP, verificado
    por su contenido."""
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insumo no encontrado.")
    data = await file.read(ITEM_PHOTO_MAX_BYTES + 1)
    if len(data) > ITEM_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="La foto supera los 4 MB.")
    tipo = _image_type(data)
    if not tipo:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Solo se aceptan fotos (JPG, PNG o WebP).")
    foto = db.query(ItemPhoto).filter(ItemPhoto.inventory_item_id == item_id).first()
    if not foto:
        foto = ItemPhoto(inventory_item_id=item_id)
        db.add(foto)
    foto.content_type, foto.size_bytes, foto.data = tipo, len(data), data
    foto.uploaded_by_user_id = current_user.id
    foto.updated_at = datetime.now(timezone.utc)
    log_audit_event(db, current_user.id, None, "item.photo", "inventory_item", item_id, {"size_bytes": len(data)})
    db.commit()
    return {"id": item_id, "photo_v": int(foto.updated_at.replace(tzinfo=foto.updated_at.tzinfo or timezone.utc).timestamp())}


@router.delete("/items/{item_id}/photo", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_photo(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.adjust")),
):
    foto = db.query(ItemPhoto).filter(ItemPhoto.inventory_item_id == item_id).first()
    if foto:
        db.delete(foto)
        log_audit_event(db, current_user.id, None, "item.photo_delete", "inventory_item", item_id, {})
        db.commit()
