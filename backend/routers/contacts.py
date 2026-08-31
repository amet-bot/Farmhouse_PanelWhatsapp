import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from database import get_db
from models.contact import Contact
from models.conversation import Conversation
from models.user import User
from schemas.contact import ContactResponse, ContactCreate, ContactUpdate
from security.auth import get_current_authorized_user
from security.access_control import check_contact_access

logger = logging.getLogger("farmhouse.contacts")

router = APIRouter(prefix="/contacts", tags=["Contactos"])

@router.get("/", response_model=List[ContactResponse])
def get_contacts(
    query: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    """
    Listado de contactos aislado por sucursal para agentes y supervisores locales (Punto 7).
    """
    db_query = db.query(Contact).filter(Contact.deleted_at.is_(None))

    if current_user.role == "agent":
        if not current_user.branch_id:
            return []
        db_query = db_query.join(Conversation, Conversation.customer_id == Contact.id).filter(
            Conversation.branch_id == current_user.branch_id,
            Conversation.deleted_at.is_(None)
        ).distinct()
    elif current_user.role == "supervisor" and current_user.branch_id:
        db_query = db_query.join(Conversation, Conversation.customer_id == Contact.id).filter(
            Conversation.branch_id == current_user.branch_id,
            Conversation.deleted_at.is_(None)
        ).distinct()

    if query:
        search = f"%{query}%"
        db_query = db_query.filter(
            (Contact.name.ilike(search)) | (Contact.phone.ilike(search))
        )
    return db_query.order_by(Contact.last_interaction.desc()).offset(skip).limit(limit).all()

@router.post("/", response_model=ContactResponse)
def create_contact(
    contact_in: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    existing = db.query(Contact).filter(Contact.phone == contact_in.phone.strip()).first()
    if existing:
        if existing.deleted_at:
            existing.deleted_at = None
            db.commit()
            db.refresh(existing)
        return existing

    contact = Contact(
        name=contact_in.name.strip(),
        phone=contact_in.phone.strip(),
        avatar_url=contact_in.avatar_url,
        notes=contact_in.notes
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact

@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    return check_contact_access(db, contact_id, current_user, action="read")

@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: int,
    contact_in: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user)
):
    contact = check_contact_access(db, contact_id, current_user, action="update")
    
    update_data = contact_in.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if isinstance(val, str):
            val = val.strip()
        setattr(contact, field, val)
    db.commit()
    db.refresh(contact)
    return contact

