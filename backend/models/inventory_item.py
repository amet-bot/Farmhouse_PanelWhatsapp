from sqlalchemy import func, Column, Integer, String, Boolean, DateTime, Numeric, ForeignKey, LargeBinary
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, deferred
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
    # Costo real por unidad de inventario, tomado del Excel de costeo de recetas (migración 069).
    # Va aparte de `reference_cost` porque la sincronización con Invu reescribe ese en cada pasada:
    # lo cargado a mano o desde el Excel se perdería. Manda sobre el de Invu cuando existe
    # (ver `effective_cost`); el costo de cada compra, cuando hay cargamento, manda sobre ambos.
    costing_cost = Column(Numeric(12, 4), nullable=True)
    costing_source = Column(String(80), nullable=True)      # de dónde salió ("Excel costeo 2026-10-05")
    costing_updated_at = Column(DateTime, nullable=True)

    @hybrid_property
    def effective_cost(self):
        """El costo de referencia que se usa para valuar cuando no hay costo de compra."""
        return self.costing_cost if self.costing_cost is not None else self.reference_cost

    @effective_cost.expression
    def effective_cost(cls):
        return func.coalesce(cls.costing_cost, cls.reference_cost)

    # Cuánto es UNA pieza entera de este insumo: en gramos, o en ml si se mide en volumen
    # (migración 047). No viene de Invu: se carga desde el panel o se aprende la primera vez que
    # alguien registra una merma de "pieza entera" ("1 baguette = 80 g").
    piece_size = Column(Numeric(12, 3), nullable=True)
    # Cuántos gramos pesa 1 ml de este insumo (migración 066). Solo hace falta cuando una receta
    # lo pide en gramos y se mide en ml (o al revés): sin esto esa línea no se puede descontar.
    # Lo pone una persona desde Recetas → Unidades; no se adivina.
    grams_per_ml = Column(Numeric(8, 4), nullable=True)

    shipment_items = relationship("ShipmentItem", back_populates="inventory_item")


class ItemPhoto(Base):
    """
    La foto de un insumo, para reconocerlo de un vistazo en la pantalla de merma rápida (/merma).
    Una por insumo. Va DENTRO de la base como las de merma y tareas (en Railway el disco se pierde
    en cada deploy); el navegador la achica antes de subirla y `data` es diferida.
    """
    __tablename__ = "item_photos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    content_type = Column(String(40), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = deferred(Column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
