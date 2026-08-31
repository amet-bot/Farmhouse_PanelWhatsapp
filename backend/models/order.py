from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_code = Column(String(50), unique=True, nullable=False, index=True) # "FH-000123"
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    order_type = Column(String(50), nullable=False, default="delivery") # "delivery", "takeout", "catering"
    status = Column(String(50), nullable=False, default="en_proceso") # "en_proceso", "en_cocina", "en_delivery", "entregado", "cancelado"
    subtotal = Column(Numeric(10, 2), nullable=False, default=0.00) # Subtotal sin delivery ni impuesto
    delivery_cost = Column(Numeric(10, 2), nullable=False, default=0.00)
    tax = Column(Numeric(10, 2), nullable=False, default=0.00) # ITBMS (7%)
    total = Column(Numeric(10, 2), nullable=False, default=0.00)
    items_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    # Relaciones
    conversation = relationship("Conversation", back_populates="orders")
    branch = relationship("Branch", back_populates="orders")
    creator_user = relationship("User", foreign_keys=[created_by])

