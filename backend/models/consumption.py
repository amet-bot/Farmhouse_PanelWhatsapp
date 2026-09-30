from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class ConsumptionRecord(Base):
    """
    "Consumo": el equipo anota cuánto se usó de cada insumo (5 kg de pollo, 2 rollos de papel).
    Es la salida de inventario que no pasa por una receta de Invu: descuenta la existencia igual
    que una merma, pero es uso normal, no pérdida.

    Cuando un insumo tiene consumo registrado a mano desde su último conteo, ese registro manda
    y el sistema deja de estimarle el uso por ventas × recetas (ver _vendido_desde_conteo en
    routers/inventory.py); si no, se sigue estimando. Así nunca se descuenta dos veces.
    """
    __tablename__ = "consumption_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    occurred_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    recorded_by_user = relationship("User", foreign_keys=[recorded_by_user_id])
    items = relationship("ConsumptionItem", back_populates="record", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_consumption_branch_occurred", "branch_id", "occurred_at"),
    )


class ConsumptionItem(Base):
    __tablename__ = "consumption_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    consumption_record_id = Column(Integer, ForeignKey("consumption_records.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)       # en la unidad del insumo
    unit_cost = Column(Numeric(12, 4), nullable=True)       # último costo conocido al registrar

    record = relationship("ConsumptionRecord", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_consumption_item_item", "inventory_item_id"),
    )
