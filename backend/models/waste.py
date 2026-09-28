from sqlalchemy import Boolean, Column, Integer, String, Numeric, DateTime, ForeignKey, Text, Index, LargeBinary
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import deferred, relationship
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
    # El peso de lo que se descartó, tal como se leyó en la balanza (migración 044). Aparte de la
    # cantidad de cada insumo: esa va en la unidad del insumo (una piña se cuenta por unidad) y
    # el peso es lo que respalda la merma, junto con la foto de evidencia.
    weight_value = Column(Numeric(10, 3), nullable=True)
    weight_unit = Column(String(5), nullable=True)   # "kg" | "g" | "lb"
    # True si ese peso se calculó con el peso promedio de una pieza y no con la balanza (048).
    weight_estimated = Column(Boolean, nullable=True)
    # Solo en recortes (merma de proceso): cuánto se limpió en total, para el rendimiento
    # ("de 5 kg de pollo quedaron 0.270 kg de recorte" → rinde 94.6 %). Migración 046.
    processed_value = Column(Numeric(10, 3), nullable=True)
    processed_unit = Column(String(5), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    recorded_by_user = relationship("User", foreign_keys=[recorded_by_user_id])
    items = relationship("WasteItem", back_populates="waste_record", cascade="all, delete-orphan")
    photos = relationship("WastePhoto", back_populates="waste_record", cascade="all, delete-orphan",
                          order_by="WastePhoto.id")

    __table_args__ = (
        Index("ix_waste_branch_occurred", "branch_id", "occurred_at"),
    )


class WastePhoto(Base):
    """
    Foto de respaldo de una merma (típicamente el peso en la balanza).

    La imagen va DENTRO de la base y no en el disco del servidor: los adjuntos de WhatsApp viven
    en backend/media, que en Railway se pierde en cada deploy si no hay un volumen montado, y una
    prueba de merma no puede depender de eso. El navegador la achica antes de subirla (~300 KB),
    así que pesa poco. `data` es diferida: listar mermas nunca trae los bytes, solo pedir la foto.
    """
    __tablename__ = "waste_photos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    waste_record_id = Column(Integer, ForeignKey("waste_records.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(40), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = deferred(Column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    waste_record = relationship("WasteRecord", back_populates="photos")
    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by_user_id])


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
    unit_cost = Column(Numeric(12, 4), nullable=True)
    # Qué se botó (migración 047): "entera" = piezas completas (`pieces` de ellas) o "parte" = un
    # pedazo o residuo pesado en balanza. NULL en las mermas anteriores. `quantity` sigue siendo
    # siempre la cantidad en la unidad del insumo: la existencia y el costo no cambian de fórmula.
    mode = Column(String(10), nullable=True)
    pieces = Column(Numeric(10, 3), nullable=True)
    # Lo que marcó la balanza para esta línea (g, o ml si es de volumen), si se pesó (migración
    # 048). En una "pieza entera" sin esto, el peso sale del promedio de la pieza: es estimado.
    measured_amount = Column(Numeric(12, 3), nullable=True)

    waste_record = relationship("WasteRecord", back_populates="items")
    inventory_item = relationship("InventoryItem")

    __table_args__ = (
        Index("ix_waste_item_inventory", "inventory_item_id"),
    )
