"""
Etiquetas de cliente del Centro WhatsApp.

Ver la lista: cualquier usuario con sesión. Crear/editar/borrar etiquetas: `tags.manage`
(supervisores y admin). Ponérselas a un cliente: `tags.assign` (todos los agentes), siempre
dentro del alcance por sucursal de `check_contact_access`.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models.contact_tag import ContactTag
from models.user import User
from schemas.contact import ContactResponse
from schemas.contact_tag import ContactTagCreate, ContactTagUpdate, ContactTagResponse, ContactTagsAssign
from security.access_control import check_contact_access
from security.auth import get_current_user, get_current_authorized_user
from security.permissions import require_permission
from services.audit import log_audit_event

logger = logging.getLogger("farmhouse.tags")

router = APIRouter(prefix="/tags", tags=["Etiquetas"])


def _check_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    q = db.query(ContactTag).filter(ContactTag.name == name)
    if exclude_id:
        q = q.filter(ContactTag.id != exclude_id)
    if q.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Ya existe la etiqueta «{name}».")


@router.get("/", response_model=List[ContactTagResponse])
def list_tags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ContactTag).order_by(ContactTag.name.asc()).all()


@router.post("/", response_model=ContactTagResponse, dependencies=[Depends(require_permission("tags.manage"))])
def create_tag(data: ContactTagCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_authorized_user)):
    _check_name_free(db, data.name)
    tag = ContactTag(name=data.name, color=data.color)
    db.add(tag)
    db.flush()
    log_audit_event(db, current_user.id, None, "tag.created", "contact_tag", tag.id, {"name": tag.name})
    db.commit()
    db.refresh(tag)
    return tag


@router.put("/{tag_id}", response_model=ContactTagResponse, dependencies=[Depends(require_permission("tags.manage"))])
def update_tag(tag_id: int, data: ContactTagUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_authorized_user)):
    tag = db.query(ContactTag).filter(ContactTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada.")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != tag.name:
        _check_name_free(db, changes["name"], exclude_id=tag.id)
    for field, value in changes.items():
        setattr(tag, field, value)
    log_audit_event(db, current_user.id, None, "tag.updated", "contact_tag", tag.id, {"fields": sorted(changes.keys())})
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("tags.manage"))])
def delete_tag(tag_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_authorized_user)):
    tag = db.query(ContactTag).filter(ContactTag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada.")
    log_audit_event(db, current_user.id, None, "tag.deleted", "contact_tag", tag.id, {"name": tag.name, "contacts": len(tag.contacts)})
    # Se quita de todos los clientes que la tenían (la relación borra los enlaces).
    tag.contacts = []
    db.delete(tag)
    db.commit()
    return None


@router.put("/contacts/{contact_id}", response_model=ContactResponse, dependencies=[Depends(require_permission("tags.assign"))])
def assign_contact_tags(
    contact_id: int,
    data: ContactTagsAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Deja al cliente exactamente con las etiquetas indicadas (reemplaza el conjunto)."""
    contact = check_contact_access(db, contact_id, current_user, action="tag")
    wanted = sorted(set(data.tag_ids))
    tags = db.query(ContactTag).filter(ContactTag.id.in_(wanted)).all() if wanted else []
    if len(tags) != len(wanted):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Alguna de las etiquetas ya no existe. Recarga la lista.")
    before = sorted(t.id for t in contact.tags)
    contact.tags = tags
    if before != wanted:
        log_audit_event(db, current_user.id, current_user.branch_id, "contact.tags_changed", "contact", contact.id,
                        {"before": before, "after": wanted})
    db.commit()
    db.refresh(contact)
    return contact
