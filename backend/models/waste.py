from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class WasteRecord(Base):
    """
    "Merma": lo que sale de una sucursal sin haberse vendido — se venció, se golpeó, se quemó,
    se lo comió el personal.

    Es la contraparte de Shipment y cierra el ciclo: hasta ahora el sistema solo registraba
    entradas, así que no podía hablar de existencias. Con las dos puntas, la existencia de un
    insumo en una sucursal es lo que entró menos lo que salió, calculado al vuelo. No hay tabla
    de stock: un saldo guardado hay que mantenerlo al día desde cada camino que lo toca (cargar,
    corregir, borrar) y se desincroniza en el primero que alguien olvide. Contar dos columnas
    cuesta una consulta y nunca miente.

    El motivo va en el registro y no en cada renglón: una merma real es un episodio con una
    causa ("se cortó la luz el domingo"), no una lista de causas distintas. Si de verdad son dos
    causas, son dos registros, y así el reporte de "cuánto se perdió por vencimiento" suma sin
    ambigüedad.
    """
    __tablename__ = "waste_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    occurred_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    # Uno de los códigos de WASTE_REASONS (routers/inventory.py). String y no Enum de base: los
    # motivos son vocabulario de negocio y van a cambiar antes que el esquema.
    reason = Column(String(40), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    recorded_by_user = relationship("User", foreign_keys=[recorded_by_user_id])
    items = relationship("WasteItem", back_populates="waste_record", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_waste_branch_occurred", "branch_id", "occurred_at"),
    )


class WasteItem(Base):
    __tablename__ = "waste_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    waste_record_id = Column(Integer, ForeignKey("waste_records.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    # Lo que costaba esa unidad cuando entró. Se copia del último cargamento de ese insumo en esa
    # sucursal en el momento de registrar, en vez de recalcularlo después: el costo de un insumo
    # cambia con cada compra, y una merma de marzo tiene que seguir valiendo lo que valía en
    # marzo aunque el proveedor haya aumentado en abril.
    unit_cost = Column(Numeric(10, 2), nullable=True)

    waste_record = relationship("WasteRecord", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_waste_item_inventory", "inventory_item_id"),
    )
