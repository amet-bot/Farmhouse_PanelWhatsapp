from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.conversation import Conversation
from models.message import Message
from models.branch import Branch
from models.user import User
from security.access_control import check_conversation_access, check_target_branch_valid

class RoutingService:
    @staticmethod
    def assign_to_branch(db: Session, conversation_id: int, branch_id: int) -> Conversation:
        conv = db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None)
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        branch = check_target_branch_valid(db, branch_id)
            
        conv.branch_id = branch_id
        conv.assigned_user_id = None
        conv.status = "unassigned"
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(conv)
        return conv

    @staticmethod
    def take_conversation(db: Session, conversation_id: int, user: User) -> Conversation:
        # Validar acceso de lectura/sucursal
        conv = check_conversation_access(db, conversation_id, user, action="take")

        if conv.branch_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Esta conversación todavía no tiene sucursal asignada (el cliente no ha elegido ninguna). Espera a que elija, o transfiérela manualmente a una sucursal."
            )

        # Control de concurrencia optimista (Punto 20)
        # Si ya fue tomada por otro agente activo, rechazar con 409 Conflict
        if conv.assigned_user_id is not None and conv.assigned_user_id != user.id:
            assigned_user_name = conv.assigned_user.name if conv.assigned_user else "otro agente"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Esta conversación ya fue tomada por {assigned_user_name}."
            )
            
        conv.assigned_user_id = user.id
        conv.status = "open"
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(conv)
        return conv

    @staticmethod
    def transfer_conversation(
        db: Session,
        conversation_id: int,
        target_branch_id: int,
        transferred_by: User,
        reason: Optional[str] = None
    ) -> Conversation:
        # Validar que el usuario que transfiere tenga acceso a la conversación actual (Punto 3)
        conv = check_conversation_access(db, conversation_id, transferred_by, action="transfer")
            
        target_branch = check_target_branch_valid(db, target_branch_id)
        old_branch_name = conv.branch.name if conv.branch else "Sin asignar"
        
        conv.branch_id = target_branch_id
        conv.assigned_user_id = None
        conv.status = "unassigned"
        conv.updated_at = datetime.now(timezone.utc)
        
        # Registrar nota interna del sistema para trazabilidad
        note_text = f"🔄 Conversación transferida de {old_branch_name} a {target_branch.name} por {transferred_by.name}."
        if reason:
            note_text += f" Motivo: {reason.strip()}"
            
        audit_message = Message(
            conversation_id=conv.id,
            direction="outgoing",
            sender_type="system",
            sender_id=transferred_by.id,
            content=note_text,
            is_internal=True,
            status="sent"
        )
        db.add(audit_message)
        db.commit()
        db.refresh(conv)
        return conv

