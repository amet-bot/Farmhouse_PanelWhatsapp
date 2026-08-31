from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(String(50), unique=True, nullable=False, index=True) # "FH-DEVICE-A82F91"
    name = Column(String(150), nullable=False) # "Tablet Clayton 01", "PC Obarrio 01"
    device_type = Column(String(50), nullable=False) # "computadora", "tablet", "celular"
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(50), nullable=False, default="active") # "active", "disabled", "revoked"
    ip_address = Column(String(50), nullable=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relaciones
    branch = relationship("Branch", back_populates="devices")
    assigned_user = relationship("User", back_populates="assigned_devices")
