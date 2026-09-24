from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class StockCount(Base):
    """
    "Conteo": alguien fue al estante, contó lo que había y lo anotó.

    Es la tercera punta de la existencia. Entradas y merma son movimientos que alguien avisa; el
    conteo es la foto de lo que de verdad hay, y la diferencia entre esa foto y lo que el sistema
    creía es lo que se movió sin que nadie lo registrara. El primer conteo de una sucursal hace
    además de inventario de arranque: el sistema empezó a contar entradas cuando ya había
    mercadería en los estantes, y esa es la forma de cargarla.

    No se guarda un saldo nuevo, se guarda la diferencia (`StockCountItem.difference`), y la
    existencia pasa a ser entradas - merma + diferencias de conteo, sumado al vuelo como hasta
    ahora. Así el cálculo sigue siendo el mismo en todos lados y nunca hay dos números que
    reconciliar.

    La fecha la pone el servidor y no se puede elegir: la diferencia se calcula contra la
    existencia del momento en que se guarda, y un conteo con fecha de la semana pasada estaría
    comparando con un número que no es el de esa fecha.
    """
    __tablename__ = "stock_counts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    counted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    counted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    counted_by_user = relationship("User", foreign_keys=[counted_by_user_id])
    items = relationship("StockCountItem", back_populates="stock_count", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_stock_count_branch_counted", "branch_id", "counted_at"),
    )


class StockCountItem(Base):
    __tablename__ = "stock_count_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    stock_count_id = Column(Integer, ForeignKey("stock_counts.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    # Lo que decía el sistema justo antes de contar y lo que se contó. Se guardan los dos, no solo
    # la diferencia: "faltaban 3 kg" no dice nada sin saber si eran 3 de 50 o 3 de 4.
    expected_quantity = Column(Numeric(10, 3), nullable=False)
    counted_quantity = Column(Numeric(10, 3), nullable=False)
    # counted - expected. Es lo que suma a la existencia; negativo es lo que faltó.
    difference = Column(Numeric(10, 3), nullable=False)
    # Costo del último cargamento de ese insumo en esa sucursal al momento de contar, copiado por
    # la misma razón que en la merma: la diferencia de marzo vale lo que valía en marzo.
    unit_cost = Column(Numeric(10, 2), nullable=True)

    stock_count = relationship("StockCount", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_stock_count_item_inventory", "inventory_item_id"),
    )
