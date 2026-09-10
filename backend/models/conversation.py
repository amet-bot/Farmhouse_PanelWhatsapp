from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(50), nullable=False, default="new") # "new", "unassigned", "open", "pending", "closed"
    delivery_type = Column(String(20), nullable=True)
    payment_method = Column(String(20), nullable=True)
    # Phone Number ID de Meta que recibió el mensaje. Permite responder desde el mismo número
    # cuando la aplicación tiene más de una línea o cambia el número enrutado.
    whatsapp_phone_number_id = Column(String(50), nullable=True, index=True)
    last_branch_prompt_at = Column(DateTime, nullable=True)
    automation_paused = Column(Boolean, default=False, nullable=False)
    # Preguntas guiadas del Pedido Corporativo/Evento (opción 4) antes de pasarle el chat a Sol:
    # corporate_intake_step = 1..4 mientras se espera la respuesta a esa pregunta, None si no
    # aplica o ya terminó. corporate_intake_notes acumula las respuestas en texto legible para
    # el resumen interno que ve Sol al recibir la conversación.
    corporate_intake_step = Column(Integer, nullable=True)
    corporate_intake_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relaciones
    contact = relationship("Contact", back_populates="conversations")
    branch = relationship("Branch", back_populates="conversations")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id], back_populates="assigned_conversations")
    deleter_user = relationship("User", foreign_keys=[deleted_by])
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    # Más reciente primero: el panel siempre debe leer orders[0] como "el pedido/carrito
    # vigente" (confirmado o carrito activo), nunca el más antiguo.
    orders = relationship("Order", back_populates="conversation", order_by="Order.created_at.desc()")
