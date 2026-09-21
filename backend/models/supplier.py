from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Supplier(Base):
    """
    Catálogo de proveedores de "Inventario y Abastecimiento" — antes era un campo de texto libre
    en Shipment.supplier; pasó a catálogo real (con autocomplete + crear-al-vuelo, igual que
    InventoryItem) para no repetir el mismo proveedor escrito distinto cada vez.
    """
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    phone = Column(String(30), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    shipments = relationship("Shipment", back_populates="supplier")
