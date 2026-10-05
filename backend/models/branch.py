from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric, true
from datetime import datetime
from database import Base

class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(50), unique=True, nullable=False)
    color = Column(String(20), nullable=True, default="#16a34a")
    active = Column(Boolean, default=True, nullable=False)
    address = Column(String(255), nullable=True)
    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    accepts_delivery = Column(Boolean, default=True, nullable=False)
    # False = NO se muestra a los clientes (menú digital ni bot de WhatsApp) ni recibe pedidos públicos: es el
    # caso de una oficina (p. ej. "Bloc") que existe en el sistema pero no vende. Sigue disponible en Administración.
    visible_to_customers = Column(Boolean, default=True, nullable=False, server_default=true())
    # Horario de atención ("HH:MM", hora de Panamá). Con esto el bot sabe si la sucursal está
    # cerrada en este momento y se lo dice al cliente antes de mandarle el menú (ver
    # services/branch_hours.py). Vacío = se asume el horario general (8:00 a 21:30).
    opens_at = Column(String(5), nullable=True)
    closes_at = Column(String(5), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relaciones
    # Sin cascade de borrado: la FK real es ON DELETE SET NULL (users.branch_id es nullable),
    # por lo que eliminar una sucursal nunca debe eliminar a sus usuarios.
    users = relationship("User", back_populates="branch")
    devices = relationship("Device", back_populates="branch")
    conversations = relationship("Conversation", back_populates="branch")
    orders = relationship("Order", back_populates="branch")
    shipments = relationship("Shipment", back_populates="branch")
