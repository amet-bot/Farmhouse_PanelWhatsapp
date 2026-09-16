from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta, timezone
from database import Base

# Sin scheduler/cron en el proyecto (mismo criterio que expire_stale_carts_for_conversations
# en services/active_cart.py): el recordatorio se calcula al vuelo cada vez que el panel pide
# el listado o el detalle de conversaciones, en vez de con un job en segundo plano.
REMINDER_THRESHOLD_MINUTES = 5

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
    # True mientras se espera que el cliente describa en texto libre qué quiere pedir, tras
    # tocar "Pedir y pagar por chat" (alternativa al Menú Digital web). Se apaga en cuanto
    # llega esa descripción, y de ahí en adelante el método de pago se resuelve con el mismo
    # campo payment_method de siempre.
    awaiting_chat_order_description = Column(Boolean, nullable=True)
    # Última vez que alguien de la sucursal abrió esta conversación (GET /conversations/{id},
    # tanto al seleccionarla como en la sincronización silenciosa mientras sigue abierta en
    # pantalla). Junto con needs_reminder, permite avisarle al panel "recuerda responder" si
    # el último mensaje es del cliente y nadie la ha abierto desde que llegó.
    last_opened_at = Column(DateTime, nullable=True)
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

    @property
    def needs_reminder(self) -> bool:
        """
        True si el último mensaje visible es del cliente (entrante, no una nota interna),
        pasaron >= REMINDER_THRESHOLD_MINUTES desde que llegó, y nadie de la sucursal ha
        abierto la conversación desde entonces (last_opened_at nulo o anterior a ese mensaje).
        """
        if self.status == "closed":
            return False

        visible_messages = [m for m in self.messages if m.deleted_at is None and not m.is_internal]
        if not visible_messages:
            return False

        last_msg = visible_messages[-1]  # self.messages ya viene ordenado por created_at asc.
        if last_msg.direction != "incoming":
            return False

        if self.last_opened_at is not None and self.last_opened_at >= last_msg.created_at:
            return False

        # Naive UTC a propósito, igual que en services/active_cart.py: las columnas DATETIME
        # de MySQL no conservan tzinfo, así que comparar contra un datetime "aware" revienta.
        age = datetime.utcnow() - last_msg.created_at
        return age >= timedelta(minutes=REMINDER_THRESHOLD_MINUTES)
