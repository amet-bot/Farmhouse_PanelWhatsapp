from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="agent") # "agent", "supervisor", "admin"
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    avatar_url = Column(String(255), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relaciones
    branch = relationship("Branch", back_populates="users")
    assigned_devices = relationship("Device", back_populates="assigned_user", foreign_keys="[Device.assigned_user_id]")
    assigned_conversations = relationship("Conversation", back_populates="assigned_user", foreign_keys="[Conversation.assigned_user_id]")

