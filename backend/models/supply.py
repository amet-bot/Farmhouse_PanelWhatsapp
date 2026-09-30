from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class ItemBranchSetting(Base):
    """
    Cómo se repone un insumo en una sucursal: mínimo (por debajo avisa), par (a cuánto se
    repone), proveedor preferido y días que tarda en llegar. Es por sucursal porque Costa del
    Este vende el doble que Clayton y no puede tener los mismos pares. Sin fila, el pedido
    sugerido se calcula solo con el ritmo de uso.
    """
    __tablename__ = "item_branch_settings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    min_quantity = Column(Numeric(10, 3), nullable=True)
    par_quantity = Column(Numeric(10, 3), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    lead_days = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    inventory_item = relationship("InventoryItem")
    branch = relationship("Branch")
    supplier = relationship("Supplier")

    __table_args__ = (
        UniqueConstraint("inventory_item_id", "branch_id", name="uq_item_branch_setting"),
        Index("ix_item_branch_setting_branch", "branch_id"),
    )


class ExpectedShipmentItem(Base):
    """
    Una línea de una orden de compra (cargamento esperado con cantidades). Al recibir, la
    pantalla de recepción arranca con estas líneas como "facturado" y el equipo marca lo que
    de verdad llegó.
    """
    __tablename__ = "expected_shipment_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    expected_shipment_id = Column(Integer, ForeignKey("expected_shipments.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_cost = Column(Numeric(12, 4), nullable=True)

    expected_shipment = relationship("ExpectedShipment", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_expected_item_expected", "expected_shipment_id"),
    )
