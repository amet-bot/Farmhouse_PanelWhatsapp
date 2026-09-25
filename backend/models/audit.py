from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class AuditEvent(Base):
    """
    Fase 4 (cuarto bloque): quién hizo qué y cuándo, en las escrituras que ya importan auditar
    (usuarios/permisos, transferencias, mermas, conteos, cargamentos). Se escribe a mano desde
    cada router que ya escribe esos cambios, en vez de un middleware genérico que interceptara
    cualquier escritura sin distinguir cuáles valen la pena — eso hubiera significado tocar todos
    los endpoints igual, sin ganar nada en simpleza.
    """
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    # Vocabulario tipo "user.create", "transfer.dispatch", "waste.create" — texto y no Enum,
    # mismo criterio que WasteRecord.reason: esto es vocabulario de negocio, va a crecer.
    action = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    actor_user = relationship("User")
    branch = relationship("Branch")

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_branch_created", "branch_id", "created_at"),
    )
