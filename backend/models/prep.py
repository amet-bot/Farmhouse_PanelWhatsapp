from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, ForeignKey, Text, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

"""
Prep por estación (hoy solo "Bowls", una por sucursal — la lista de ingredientes y sus pars no
es la misma en todas). Calcado de la hoja de papel que ya usan en cocina: ítems agrupados por
sección (Base, Toppings, Dressings...) con un par objetivo, y un checklist que se llena varias
veces al día comparando cuánto hay contra ese par.

Los checkpoints (10am/3pm/8pm en el papel, pero "Congelador Grande"/"Congelador chico" para los
kits de smoothie) no se hardcodean: cada plantilla define los suyos, para no inventar un segundo
sistema para lo que en el papel ya es la misma idea con otro nombre de columna.

"Listo" no se guarda a mano (en el papel esa columna nunca se llenaba) — se calcula comparando
on_hand contra el par en el momento de mostrarlo.
"""


class PrepTemplate(Base):
    __tablename__ = "prep_templates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    name = Column(String(100), nullable=False, default="Bowls")
    # Lista ordenada de nombres de checkpoint, en JSON: ["10am", "3pm", "8pm"]
    checkpoints_json = Column(Text, nullable=False)
    active = Column(String(1), nullable=False, default="Y")  # "Y"/"N" — soft delete simple
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=True)

    branch = relationship("Branch")
    items = relationship("PrepTemplateItem", back_populates="template", cascade="all, delete-orphan", order_by="PrepTemplateItem.sort_order")

    __table_args__ = (
        Index("ix_prep_template_branch", "branch_id"),
    )


class PrepTemplateItem(Base):
    __tablename__ = "prep_template_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("prep_templates.id", ondelete="CASCADE"), nullable=False)
    section = Column(String(60), nullable=False)   # "Base", "Toppings", "Dressings"...
    name = Column(String(150), nullable=False)
    unit_label = Column(String(60), nullable=True)  # "repuesto", "cambro", "peso 500gr", "unidades"...
    par_target = Column(Numeric(10, 2), nullable=True)
    notes = Column(Text, nullable=True)             # nota de prep, calcada de "Comentarios"
    sort_order = Column(Integer, nullable=False, default=0)

    template = relationship("PrepTemplate", back_populates="items")

    __table_args__ = (
        Index("ix_prep_item_template", "template_id"),
    )


class PrepCheck(Base):
    """Una pasada completa: esta plantilla, este checkpoint, este día."""
    __tablename__ = "prep_checks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("prep_templates.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    checkpoint = Column(String(60), nullable=False)
    check_date = Column(Date, nullable=False)
    filled_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=True)

    template = relationship("PrepTemplate")
    branch = relationship("Branch")
    filled_by_user = relationship("User")
    entries = relationship("PrepCheckEntry", back_populates="check", cascade="all, delete-orphan")

    __table_args__ = (
        # Un solo llenado por plantilla+checkpoint+día — volver a enviar actualiza el existente
        # en vez de duplicarlo (igual que corregir un error a media tarde).
        UniqueConstraint("template_id", "checkpoint", "check_date", name="uq_prep_check_slot"),
        Index("ix_prep_check_branch_date", "branch_id", "check_date"),
    )


class PrepCheckEntry(Base):
    __tablename__ = "prep_check_entries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    check_id = Column(Integer, ForeignKey("prep_checks.id", ondelete="CASCADE"), nullable=False)
    template_item_id = Column(Integer, ForeignKey("prep_template_items.id"), nullable=False)
    on_hand = Column(Numeric(10, 2), nullable=False)
    note = Column(Text, nullable=True)

    check = relationship("PrepCheck", back_populates="entries")
    template_item = relationship("PrepTemplateItem")

    __table_args__ = (
        UniqueConstraint("check_id", "template_item_id", name="uq_prep_entry_item"),
    )
