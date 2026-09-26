from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

# Tipo de insumo (columna `kind`). Viene de Invu: un ingrediente con subreceta es una preparación
# hecha en la casa (salsas, arroces, Avo Smash...); el resto es materia prima que se compra.
KIND_RAW = "materia_prima"
KIND_HOUSE = "casa"


class InventoryItem(Base):
    """
    Catálogo de "Inventario y Abastecimiento" — insumos crudos y productos ya armados conviven en
    la misma lista a propósito (ver plan de Cargamento): una cocina real recibe de todo, no solo
    ingredientes, y separar rígido hubiera sido más estructura de la que este primer boceto pide.
    Compartido entre todas las sucursales (no hay un catálogo por sucursal).

    Los que vienen de Invu (sus "Ingredientes") traen `invu_id` y se mantienen al día solos
    (services/invu_items_sync.py); los cargados a mano siguen con invu_id en NULL.
    """
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    unit = Column(String(30), nullable=False) # texto libre: "kg", "lb", "unidad", "caja", "litro"...
    category = Column(String(50), nullable=True) # texto libre y opcional: "Insumo", "Empaque"...
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Lo que viene de Invu (migración 043).
    invu_id = Column(Integer, nullable=True, unique=True, index=True)
    code = Column(String(50), nullable=True)           # P204...
    kind = Column(String(20), nullable=True)           # KIND_RAW / KIND_HOUSE; NULL = sin clasificar
    # El costo que Invu tiene cargado, por unidad de inventario. Solo referencia: el costo real
    # de cada compra es el que se anota en el cargamento.
    reference_cost = Column(Numeric(12, 4), nullable=True)
    synced_at = Column(DateTime, nullable=True)

    shipment_items = relationship("ShipmentItem", back_populates="inventory_item")
