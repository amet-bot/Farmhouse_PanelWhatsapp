import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.order import Order
from models.branch import Branch
from models.user import User
from schemas.contact import ContactResponse, ContactCreate, ContactUpdate
from security.auth import get_current_authorized_user
from security.access_control import check_contact_access

logger = logging.getLogger("farmhouse.contacts")

router = APIRouter(prefix="/contacts", tags=["Contactos"])


def _scoped_contacts_query(db: Session, current_user: User):
    """Aplica el mismo aislamiento por sucursal al directorio y a sus exportaciones."""
    query = db.query(Contact).filter(Contact.deleted_at.is_(None))
    if current_user.role == "agent" or (
        current_user.role == "supervisor" and current_user.branch_id
    ):
        if not current_user.branch_id:
            return query.filter(False)
        query = query.join(
            Conversation, Conversation.customer_id == Contact.id
        ).filter(Conversation.branch_id == current_user.branch_id).distinct()
    return query


def _contact_directory_row(db: Session, contact: Contact, current_user: User) -> dict:
    conversations = db.query(Conversation).filter(Conversation.customer_id == contact.id)
    if current_user.role in ("agent", "supervisor") and current_user.branch_id:
        conversations = conversations.filter(Conversation.branch_id == current_user.branch_id)
    conversation_rows = conversations.order_by(Conversation.updated_at.desc()).all()
    conversation_ids = [conversation.id for conversation in conversation_rows]
    active_rows = [conversation for conversation in conversation_rows if not conversation.deleted_at]
    latest = conversation_rows[0] if conversation_rows else None
    latest_active = active_rows[0] if active_rows else None
    order_count = 0
    if conversation_ids:
        order_count = db.query(Order).filter(
            Order.conversation_id.in_(conversation_ids),
            Order.deleted_at.is_(None),
            Order.status != "carrito_activo",
        ).count()

    branch = db.query(Branch).filter(Branch.id == latest.branch_id).first() if latest and latest.branch_id else None
    return {
        "id": contact.id,
        "name": contact.name,
        "phone": contact.phone,
        "notes": contact.notes,
        "address": contact.address,
        "created_at": contact.created_at,
        "last_interaction": contact.last_interaction,
        "conversation_count": len(conversation_rows),
        "active_conversation_count": len(active_rows),
        "archived_conversation_count": len(conversation_rows) - len(active_rows),
        "order_count": order_count,
        "latest_conversation_id": latest.id if latest else None,
        "latest_active_conversation_id": latest_active.id if latest_active else None,
        "latest_conversation_status": latest.status if latest else None,
        "latest_branch_name": branch.name if branch else None,
    }


def _directory_rows(db: Session, current_user: User, query: Optional[str] = None) -> list[dict]:
    contacts_query = _scoped_contacts_query(db, current_user)
    if query:
        search = f"%{query.strip()}%"
        contacts_query = contacts_query.filter(
            (Contact.name.ilike(search)) | (Contact.phone.ilike(search))
        )
    contacts = contacts_query.order_by(Contact.last_interaction.desc()).limit(1000).all()
    return [_contact_directory_row(db, contact, current_user) for contact in contacts]


@router.get("/directory")
def get_contact_directory(
    query: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Directorio enriquecido de clientes, incluso si sus chats fueron archivados."""
    return _directory_rows(db, current_user, query)


@router.get("/export.csv")
def export_contacts_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Descarga la agenda visible para el usuario en un CSV compatible con Excel."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Nombre", "Telefono", "Primera vez", "Ultima interaccion", "Sucursal",
        "Conversaciones", "Chats archivados", "Pedidos", "Direccion", "Notas",
    ])
    for row in _directory_rows(db, current_user):
        writer.writerow([
            row["name"], row["phone"], row["created_at"], row["last_interaction"],
            row["latest_branch_name"] or "", row["conversation_count"],
            row["archived_conversation_count"], row["order_count"],
            row["address"] or "", row["notes"] or "",
        ])
    filename = f"contactos-farmhouse-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/chats.json")
def export_chats_json(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_authorized_user),
):
    """Exporta conversaciones y mensajes en un formato portable y apto para análisis posterior."""
    contacts = _scoped_contacts_query(db, current_user).order_by(Contact.last_interaction.desc()).all()
    exported_contacts = []
    for contact in contacts:
        conv_query = db.query(Conversation).filter(Conversation.customer_id == contact.id)
        if current_user.role in ("agent", "supervisor") and current_user.branch_id:
            conv_query = conv_query.filter(Conversation.branch_id == current_user.branch_id)
        conversations = []
        for conversation in conv_query.order_by(Conversation.created_at.asc()).all():
            messages = db.query(Message).filter(
                Message.conversation_id == conversation.id
            ).order_by(Message.created_at.asc()).all()
            conversations.append({
                "id": conversation.id,
                "status": conversation.status,
                "archived": bool(conversation.deleted_at),
                "branch": conversation.branch.name if conversation.branch else None,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "messages": [{
                    "direction": message.direction,
                    "sender_type": message.sender_type,
                    "content": message.content,
                    "is_internal": bool(message.is_internal),
                    "status": message.status,
                    "media_type": message.media_type,
                    "created_at": message.created_at,
                    "archived": bool(message.deleted_at),
                } for message in messages],
            })
        exported_contacts.append({
            "id": contact.id,
            "name": contact.name,
            "phone": contact.phone,
            "first_contact": contact.created_at,
            "last_interaction": contact.last_interaction,
            "notes": contact.notes,
            "address": contact.address,
            "conversations": conversations,
        })

    payload = {
        "format": "farmhouse-conversations-v1",
        "exported_at": datetime.now(timezone.utc),
        "contains_personal_data": True,
        "analysis_notice": "Este archivo contiene conversaciones privadas. Compartir solo con servicios autorizados.",
        "contacts": exported_contacts,
    }
    filename = f"conversaciones-farmhouse-{datetime.now(timezone.utc).date().isoformat()}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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

