from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class InvuMenuItem(Base):
    """
    Un plato del menú de una sucursal, copiado de Invu POS (Farmhouse Link).

    Es por sucursal porque cada sucursal es una base aparte en Invu: el mismo plato tiene el
    mismo `code` en todas ("MF320"), pero su id interno a veces cambia de una a otra. Por eso lo
    que amarra un plato entre sucursales —y lo que van a usar las recetas— es `code`, no `invu_id`.
    """
    __tablename__ = "invu_menu_items"
    __table_args__ = (
        UniqueConstraint("branch_id", "invu_id", name="uq_invu_menu_item_branch_invu"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    invu_id = Column(Integer, nullable=False)
    code = Column(String(50), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    suggested_price = Column(Numeric(10, 2), nullable=True)
    # Un plato que dejó de venir en la sincronización queda inactivo, nunca se borra: las
    # ventas viejas y su receta lo siguen nombrando.
    active = Column(Boolean, default=True, nullable=False)
    synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")


class InvuSale(Base):
    """
    Una orden de la caja de Invu, tal como vino: cerrada o nota de crédito.

    `business_date` es el día de venta en hora de Panamá según la apertura de la orden — el
    mismo corte que usa Invu para filtrar — y es por donde se consulta todo. `opened_at` y
    `closed_at` se guardan en UTC como el resto del proyecto.

    Una nota de crédito en Invu es una orden aparte ("Nota Credito" / "NC Parcial") y además
    marca "Devuelto NC" las líneas de la orden original. Para contar lo vendido alcanza con lo
    segundo (ver `InvuSaleLine.counted`); la orden de la nota se guarda igual porque su monto
    hace falta para cuadrar el total del día con el de Invu.
    """
    __tablename__ = "invu_sales"
    __table_args__ = (
        UniqueConstraint("branch_id", "invu_order_id", name="uq_invu_sale_branch_order"),
        Index("ix_invu_sale_branch_date", "branch_id", "business_date"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    invu_order_id = Column(Integer, nullable=False)
    business_date = Column(Date, nullable=False)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False)          # `pagada` de Invu: Cerrada, Nota Credito, NC Parcial
    is_credit_note = Column(Boolean, default=False, nullable=False)
    order_type = Column(String(60), nullable=True)       # Orden Normal, Pedidos Ya, Catering…
    channel = Column(String(60), nullable=True)          # INVUPOS, Deliverect…
    subtotal = Column(Numeric(10, 2), nullable=True)
    discount = Column(Numeric(10, 2), nullable=True)
    tax = Column(Numeric(10, 2), nullable=True)
    total = Column(Numeric(10, 2), nullable=True)
    synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    lines = relationship("InvuSaleLine", back_populates="sale", cascade="all, delete-orphan")


class InvuSaleLine(Base):
    """
    Un plato dentro de una orden. `branch_id` y `business_date` se repiten de la orden a
    propósito: casi toda consulta de Link es "cuánto de cada plato, por sucursal y día", y así
    sale de una sola tabla.
    """
    __tablename__ = "invu_sale_lines"
    __table_args__ = (
        UniqueConstraint("sale_id", "invu_line_id", name="uq_invu_sale_line"),
        Index("ix_invu_sale_line_branch_date", "branch_id", "business_date"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sale_id = Column(Integer, ForeignKey("invu_sales.id", ondelete="CASCADE"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    business_date = Column(Date, nullable=False)
    invu_line_id = Column(Integer, nullable=False)
    invu_item_id = Column(Integer, nullable=True)
    code = Column(String(50), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=True)
    quantity = Column(Numeric(10, 3), nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=True)
    discount = Column(Numeric(10, 2), nullable=True)
    total = Column(Numeric(10, 2), nullable=True)
    status = Column(String(30), nullable=True)           # `desc_status_venta_item`: Agregado, Devuelto NC…
    # Si esta línea cuenta como vendida: línea "Agregado" dentro de una orden que no es nota de
    # crédito. Se decide al sincronizar para que ninguna consulta tenga que repetir la regla.
    counted = Column(Boolean, default=True, nullable=False)

    sale = relationship("InvuSale", back_populates="lines")
    modifiers = relationship("InvuSaleModifier", back_populates="line", cascade="all, delete-orphan")


class InvuSaleModifier(Base):
    """
    Lo que se eligió dentro del plato ("Salmon Bulgogi", "Large"). Para las recetas importa
    tanto como el plato: un bowl con salmón no gasta lo mismo que uno con pollo.
    `quantity` es la de toda la línea, no por unidad (2 bowls con salmón → 2).
    """
    __tablename__ = "invu_sale_modifiers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    line_id = Column(Integer, ForeignKey("invu_sale_lines.id", ondelete="CASCADE"), nullable=False, index=True)
    invu_modifier_id = Column(Integer, nullable=True)
    code = Column(String(50), nullable=True)
    name = Column(String(200), nullable=False)
    quantity = Column(Numeric(10, 3), nullable=False)
    total = Column(Numeric(10, 2), nullable=True)

    line = relationship("InvuSaleLine", back_populates="modifiers")


class InvuSyncDay(Base):
    """
    Bitácora de sincronización: un renglón por sucursal y día. Dice qué días ya se trajeron
    (para saber qué falta del historial) y si lo nuestro cuadra con el cierre de Invu.

    `net_total` es la suma de órdenes cerradas menos notas de crédito, calculada acá;
    `invu_total` es el total del día según Invu. Si no coinciden, algo se perdió en el camino y
    el día se vuelve a pedir.
    """
    __tablename__ = "invu_sync_days"
    __table_args__ = (
        UniqueConstraint("branch_id", "business_date", name="uq_invu_sync_day"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    business_date = Column(Date, nullable=False)
    orders_count = Column(Integer, default=0, nullable=False)
    lines_count = Column(Integer, default=0, nullable=False)
    net_total = Column(Numeric(12, 2), nullable=True)
    invu_total = Column(Numeric(12, 2), nullable=True)
    matches = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)
    synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
