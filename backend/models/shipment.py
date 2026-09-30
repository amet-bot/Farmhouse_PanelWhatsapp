from sqlalchemy import Boolean, Column, Date, Integer, LargeBinary, Numeric, DateTime, ForeignKey, String, Text, Index
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import deferred, relationship
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
    # Recibir contra factura (todo opcional: los cargamentos viejos no lo tienen).
    invoice_number = Column(String(60), nullable=True)
    # True si algún renglón no llegó como decía la factura; None en los cargamentos de antes.
    has_issues = Column(Boolean, nullable=True)
    # La incidencia que se abrió sola por las diferencias (Operación de Sucursal).
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True)

    branch = relationship("Branch", back_populates="shipments")
    received_by_user = relationship("User", foreign_keys=[received_by_user_id])
    supplier = relationship("Supplier", back_populates="shipments")
    items = relationship("ShipmentItem", back_populates="shipment", cascade="all, delete-orphan")
    photos = relationship("ShipmentPhoto", back_populates="shipment", cascade="all, delete-orphan",
                          order_by="ShipmentPhoto.id")
    # El cargamento agendado que se recibió con este (si lo hubo). Solo lectura: el enlace vive en
    # expected_shipments.shipment_id.
    expected_shipment = relationship("ExpectedShipment", uselist=False, viewonly=True)


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_cost = Column(Numeric(12, 4), nullable=True) # opcional: alimenta Gasto por sucursal a futuro
    # Lo que dice la factura (None: no se comparó) y cómo llegó ese renglón:
    # ok | falto | sobro | equivocado | danado. `quantity` es siempre lo que entró de verdad.
    invoiced_quantity = Column(Numeric(10, 3), nullable=True)
    line_status = Column(String(20), nullable=True)
    line_note = Column(String(200), nullable=True)

    shipment = relationship("Shipment", back_populates="items")
    inventory_item = relationship("InventoryItem", back_populates="shipment_items")


class ShipmentPhoto(Base):
    """Foto de la factura (o de lo que llegó mal) de un cargamento. Mismo criterio que WastePhoto."""
    __tablename__ = "shipment_photos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(40), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = deferred(Column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    shipment = relationship("Shipment", back_populates="photos")


class ExpectedShipment(Base):
    """
    Un cargamento que se espera ("Mañana llega PriceSmart, de 3 pm en adelante"). Lo agenda un
    supervisor; a la sucursal le aparece en el inicio y le llega un aviso. Al recibirlo se enlaza
    con el cargamento real (`shipment_id`, el único lado del enlace) y queda "recibido".
    """
    __tablename__ = "expected_shipments"

    STATUSES = ("pendiente", "recibido", "cancelado")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    expected_date = Column(Date, nullable=False)
    time_from = Column(String(5), nullable=True)        # "15:00": desde qué hora
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pendiente")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    branch = relationship("Branch")
    supplier = relationship("Supplier")
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    # Las líneas (cantidad y costo) cuando se agendó como orden de compra (bloque de
    # abastecimiento). Un cargamento agendado "a mano" no tiene líneas y sigue funcionando igual.
    items = relationship("ExpectedShipmentItem", back_populates="expected_shipment", cascade="all, delete-orphan",
                         order_by="ExpectedShipmentItem.id")

    __table_args__ = (
        Index("ix_expected_shipment_branch_status", "branch_id", "status"),
    )
