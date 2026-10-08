from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class QuickReply(Base):
    """
    Respuesta rápida del Centro WhatsApp: un mensaje listo que el agente inserta en el cuadro de
    texto escribiendo "/atajo" (o tocando su pastilla) y que revisa antes de enviar. Nunca se
    manda sola. `branch_id` NULL = sirve en todas las sucursales.

    En el cuerpo se pueden usar {nombre}, {sucursal} y {menu}; el frontend los reemplaza con los
    datos de la conversación abierta.
    """
    __tablename__ = "quick_replies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shortcut = Column(String(40), nullable=False, index=True)   # "horario" → se escribe /horario
    title = Column(String(120), nullable=False)
    body = Column(Text, nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    branch = relationship("Branch")
