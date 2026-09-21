from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class InventoryItem(Base):
    """
    Catálogo de "Inventario y Abastecimiento" — insumos crudos y productos ya armados conviven en
    la misma lista a propósito (ver plan de Cargamento): una cocina real recibe de todo, no solo
    ingredientes, y separar rígido hubiera sido más estructura de la que este primer boceto pide.
    Compartido entre todas las sucursales (no hay un catálogo por sucursal).
    """
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    unit = Column(String(30), nullable=False) # texto libre: "kg", "lb", "unidad", "caja", "litro"...
    category = Column(String(50), nullable=True) # texto libre y opcional: "Insumo", "Empaque"...
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    shipment_items = relationship("ShipmentItem", back_populates="inventory_item")
