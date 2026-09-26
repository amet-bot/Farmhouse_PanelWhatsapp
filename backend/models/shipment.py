from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Shipment(Base):
    """
    "Cargamento": lo que llega a una sucursal, cargado por el encargado de esa sucursal. Primer
    módulo real de Inventario y Abastecimiento (ver plan) — Merma y Gasto por sucursal se
    construyen aparte más adelante, reusando `ShipmentItem.unit_cost` para reportes de gasto.
    """
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    received_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch", back_populates="shipments")
    received_by_user = relationship("User", foreign_keys=[received_by_user_id])
    supplier = relationship("Supplier", back_populates="shipments")
    items = relationship("ShipmentItem", back_populates="shipment", cascade="all, delete-orphan")


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_cost = Column(Numeric(10, 2), nullable=True) # opcional: alimenta Gasto por sucursal a futuro

    shipment = relationship("Shipment", back_populates="items")
    inventory_item = relationship("InventoryItem", back_populates="shipment_items")
