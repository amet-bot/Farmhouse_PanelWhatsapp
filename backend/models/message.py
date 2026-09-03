from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    direction = Column(String(20), nullable=False) # "incoming", "outgoing"
    sender_type = Column(String(20), nullable=False) # "customer", "agent", "system"
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    content = Column(Text, nullable=False)
    is_internal = Column(Boolean, default=False, nullable=False)
    whatsapp_message_id = Column(String(100), nullable=True, unique=True, index=True)
    status = Column(String(20), default="sent", nullable=False) # "pending", "sent", "delivered", "read", "failed"
    error_detail = Column(String(500), nullable=True)
    media_url = Column(String(500), nullable=True)
    media_type = Column(String(20), nullable=True)  # "image", "video", "audio", "document", "sticker"
    media_mime_type = Column(String(100), nullable=True)
    # ID del objeto multimedia que devuelve WhatsApp Cloud API (message.image.id, etc.). Se
    # conserva para poder reintentar la descarga desde Meta si falló la primera vez (endpoint
    # POST /messages/{id}/retry-media) sin depender de parsear el contenido del mensaje.
    media_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relaciones
    conversation = relationship("Conversation", back_populates="messages")
    sender_user = relationship("User", foreign_keys=[sender_id])
    deleter_user = relationship("User", foreign_keys=[deleted_by])

