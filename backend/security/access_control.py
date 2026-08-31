import logging
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.conversation import Conversation
from models.contact import Contact
from models.order import Order
from models.user import User
from models.branch import Branch

logger = logging.getLogger("farmhouse.access_control")

def check_conversation_access(
    db: Session,
    conversation_id: int,
    user: User,
    action: str = "read"
) -> Conversation:
    """
    Control de acceso centralizado para conversaciones (Punto 3).
    - admin: Acceso global a todas las sucursales.
    - supervisor (global, branch_id=None): Acceso global.
    - supervisor (local, branch_id set): Solo conversaciones de su sucursal.
    - agent: Solo conversaciones de su sucursal.
    """
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.deleted_at.is_(None)
    ).first()

    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversación no encontrada."
        )

    if user.role == "admin":
        return conv

    if user.role == "supervisor" and user.branch_id is None:
        return conv

    # Si la conversación no tiene sucursal asignada todavía
    if conv.branch_id is None:
        # Permitir lectura a supervisores y agentes para enrutamiento manual
        if action in ["read", "transfer"]:
            return conv
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esta conversación aún no tiene sucursal asignada."
        )

    # Validar correspondencia de sucursal
    if conv.branch_id != user.branch_id:
        logger.warning(
            f"Acceso denegado ({action}): Usuario {user.name} [@{user.username}, Rol: {user.role}, Sucursal: {user.branch_id}] "
            f"intentó acceder a conversación ID {conversation_id} de sucursal ID {conv.branch_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes acceso a conversaciones de otra sucursal."
        )

    return conv

def check_contact_access(
    db: Session,
    contact_id: int,
    user: User,
    action: str = "read"
) -> Contact:
    """
    Control de acceso centralizado para contactos (Punto 7).
    - admin / supervisor global: Acceso a todos los contactos.
    - agent / supervisor local: Solo contactos que tengan conversaciones en su sucursal.
    """
    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.deleted_at.is_(None)
    ).first()

    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contacto no encontrado."
        )

    if user.role == "admin" or (user.role == "supervisor" and user.branch_id is None):
        return contact

    # Comprobar si el contacto tiene alguna conversación en la sucursal del usuario
    has_conv_in_branch = db.query(Conversation).filter(
        Conversation.customer_id == contact_id,
        Conversation.branch_id == user.branch_id,
        Conversation.deleted_at.is_(None)
    ).first()

    if not has_conv_in_branch:
        logger.warning(
            f"Acceso a contacto denegado ({action}): Usuario {user.name} [@{user.username}] "
            f"intentó acceder a contacto ID {contact_id} sin conversaciones en su sucursal {user.branch_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para consultar información de este contacto."
        )

    return contact

def check_order_access(
    db: Session,
    order_id: int,
    user: User,
    action: str = "read"
) -> Order:
    """
    Control de acceso centralizado para pedidos y comandas (Punto 8).
    """
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.deleted_at.is_(None)
    ).first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado."
        )

    if user.role == "admin" or (user.role == "supervisor" and user.branch_id is None):
        return order

    if order.branch_id != user.branch_id:
        logger.warning(
            f"Acceso a pedido denegado ({action}): Usuario {user.name} [@{user.username}] "
            f"intentó acceder a pedido ID {order_id} de sucursal ID {order.branch_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para acceder a comandas de otra sucursal."
        )

    return order

def check_target_branch_valid(db: Session, branch_id: int) -> Branch:
    """Verifica que la sucursal de destino exista y esté activa."""
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.active == True).first()
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La sucursal seleccionada no existe o se encuentra inactiva."
        )
    return branch
