from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(50), unique=True, nullable=False)
    color = Column(String(20), nullable=True, default="#16a34a")
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relaciones
    # Sin cascade de borrado: la FK real es ON DELETE SET NULL (users.branch_id es nullable),
    # por lo que eliminar una sucursal nunca debe eliminar a sus usuarios.
    users = relationship("User", back_populates="branch")
    devices = relationship("Device", back_populates="branch")
    conversations = relationship("Conversation", back_populates="branch")
    orders = relationship("Order", back_populates="branch")
