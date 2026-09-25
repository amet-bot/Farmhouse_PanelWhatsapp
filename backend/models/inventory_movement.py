from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class InventoryMovement(Base):
    """
    Libro de movimientos de inventario: una fila por cada entrada, salida o ajuste de conteo, con
    signo (positivo entra, negativo sale). Es una bitácora en paralelo a la fórmula que ya calcula
    la existencia al vuelo (`_on_hand_map` en routers/inventory.py) — no la reemplaza todavía. La
    idea es compararlas un tiempo antes de decidir cuál manda, así que se escribe en cada camino
    que ya escribe cargamento, merma o conteo, sin tocar la lógica de ninguno de los tres.
    """
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    # "in" (cargamento), "out" (merma) o "adjustment" (diferencia de conteo — ya viene con el
    # signo correcto: positiva es sobrante, negativa es faltante).
    movement_type = Column(String(20), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_cost = Column(Numeric(10, 2), nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    # De qué registro salió este movimiento ("shipment", "waste", "count") + su id, para poder
    # rastrear cada fila hasta su origen sin adivinar por fecha e insumo.
    source_type = Column(String(20), nullable=False)
    source_id = Column(Integer, nullable=True)
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    inventory_item = relationship("InventoryItem")
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_inv_movement_branch_item", "branch_id", "inventory_item_id"),
        Index("ix_inv_movement_source", "source_type", "source_id"),
    )
