from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Qué etiquetas tiene cada cliente (muchos a muchos).
contact_tag_links = Table(
    "contact_tag_links",
    Base.metadata,
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("contact_tags.id", ondelete="CASCADE"), primary_key=True),
)


class ContactTag(Base):
    """
    Etiqueta de cliente ("VIP", "Corporativo", "Reclamo", "Frecuente"…): una palabra con color que
    el equipo le pone al contacto y que se ve en la lista de chats y en el panel del cliente.
    Son globales (el mismo cliente puede escribir a varias sucursales).
    """
    __tablename__ = "contact_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(60), nullable=False, unique=True)
    color = Column(String(7), nullable=False, default="#16a34a")  # "#rrggbb"
    created_at = Column(DateTime, default=_now, nullable=False)

    contacts = relationship("Contact", secondary=contact_tag_links, back_populates="tags")
