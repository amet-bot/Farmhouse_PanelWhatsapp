from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(String(50), unique=True, nullable=False, index=True) # "FH-DEVICE-A82F91" (etiqueta pública, no autoriza nada)
    name = Column(String(150), nullable=False) # "Tablet Clayton 01", "PC Obarrio 01"
    device_type = Column(String(50), nullable=False) # "computadora", "tablet", "celular"
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(50), nullable=False, default="active") # "active", "disabled", "revoked"
    ip_address = Column(String(50), nullable=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    # Vinculación real del equipo (ver services/device_access.py):
    # - secret_hash: SHA-256 del token secreto que guarda el navegador vinculado. Es lo único que
    #   autoriza; el código público FH-DEVICE-… solo sirve para reconocer el equipo en las listas.
    # - enroll_code_hash / enroll_expires_at: código de vinculación de un solo uso que genera el
    #   administrador y que alguien teclea UNA vez en el equipo para obtener el token.
    secret_hash = Column(String(64), nullable=True, unique=True, index=True)
    enroll_code_hash = Column(String(64), nullable=True)
    enroll_expires_at = Column(DateTime, nullable=True)
    enrolled_at = Column(DateTime, nullable=True)

    # Relaciones
    branch = relationship("Branch", back_populates="devices")
    assigned_user = relationship("User", back_populates="assigned_devices")
