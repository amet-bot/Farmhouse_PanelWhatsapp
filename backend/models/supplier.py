from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class Supplier(Base):
    """
    Catálogo de proveedores de "Inventario y Abastecimiento" — antes era un campo de texto libre
    en Shipment.supplier; pasó a catálogo real (con autocomplete + crear-al-vuelo, igual que
    InventoryItem) para no repetir el mismo proveedor escrito distinto cada vez.

    Desde la integración con Invu POS, los proveedores de verdad viven allá: Invu es el sistema
    donde la casa los da de alta, con su RUC y su contacto. Este catálogo es una copia local que
    se sincroniza (ver services/invu_sync.py). Mientras la integración no esté configurada, el
    panel sigue siendo el que manda — no se puede dejar el sistema sin forma de cargar un
    proveedor solo porque todavía no llegaron las credenciales.
    """
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    phone = Column(String(30), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # ---- Lo que viene de Invu ----
    # `invu_id` es la marca de origen: con él, el proveedor se sincroniza y no se edita acá;
    # sin él, es uno cargado a mano en el panel y se respeta tal cual.
    invu_id = Column(Integer, unique=True, nullable=True, index=True)
    code = Column(String(50), nullable=True)
    tax_id = Column(String(50), nullable=True)          # RUC
    contact_name = Column(String(150), nullable=True)
    email = Column(String(150), nullable=True)
    delivery_day = Column(Integer, nullable=True)       # día de la semana que entrega
    synced_at = Column(DateTime, nullable=True)

    shipments = relationship("Shipment", back_populates="supplier")
