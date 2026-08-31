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
    users = relationship("User", back_populates="branch", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="branch")
    conversations = relationship("Conversation", back_populates="branch")
    orders = relationship("Order", back_populates="branch")
