from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class Transfer(Base):
    """
    Traslado de insumos de una sucursal a otra. No descuenta inventario al crearse — recién al
    despachar sale de la sucursal de origen y recién al recibir entra a la de destino, cada uno
    con su propio movimiento en el libro (transfer_out / transfer_in). Entre medio pasa por
    aprobación: quien manda el insumo confirma que efectivamente lo tiene y lo va a mandar antes
    de que nadie lo dé por hecho en destino.

    Se crea directo en "requested" (sin un estado "draft" editable separado): no hay todavía un
    caso de uso real para guardar un traslado a medio armar antes de pedirlo, y agregar esa
    edición sin que nadie la pida sería inventar alcance de más.
    """
    __tablename__ = "transfers"

    STATUSES = ("requested", "approved", "dispatched", "received", "rejected", "cancelled")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    from_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    to_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    status = Column(String(20), nullable=False, default="requested")

    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    dispatched_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    received_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    approved_at = Column(DateTime, nullable=True)
    dispatched_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    from_branch = relationship("Branch", foreign_keys=[from_branch_id])
    to_branch = relationship("Branch", foreign_keys=[to_branch_id])
    requested_by_user = relationship("User", foreign_keys=[requested_by_user_id])
    approved_by_user = relationship("User", foreign_keys=[approved_by_user_id])
    dispatched_by_user = relationship("User", foreign_keys=[dispatched_by_user_id])
    received_by_user = relationship("User", foreign_keys=[received_by_user_id])
    items = relationship("TransferItem", back_populates="transfer", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_transfer_from_branch", "from_branch_id"),
        Index("ix_transfer_to_branch", "to_branch_id"),
    )


class TransferItem(Base):
    __tablename__ = "transfer_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    transfer_id = Column(Integer, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_cost = Column(Numeric(10, 2), nullable=True)

    transfer = relationship("Transfer", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_transfer_item_inventory", "inventory_item_id"),
    )
