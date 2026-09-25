from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

"""
Operación de sucursal (Fase 4, tercer bloque): tres tablas chicas, cada una con sus propios
estados, sin motor de flujos genérico — el plan grande lo pide explícito. Comparten archivo
porque ninguna es lo bastante grande para justificar uno propio, igual que waste.py agrupa
WasteRecord y WasteItem.

- SupplyRequest ("solicitud de insumos"): un pedido de algo que hace falta, en texto libre y no
  ligado al catálogo — puede pedirse algo que el inventario todavía no tiene dado de alta.
- Incident ("incidencia"): un problema reportado en la sucursal (equipo roto, falta de personal,
  lo que sea) con una severidad y su propio ciclo de vida.
- Task ("tarea"): un pendiente asignable a alguien de la sucursal, con fecha límite opcional.
"""


class SupplyRequest(Base):
    __tablename__ = "supply_requests"

    STATUSES = ("open", "fulfilled", "cancelled")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_name = Column(String(150), nullable=False)
    quantity_hint = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    branch = relationship("Branch")
    requested_by_user = relationship("User", foreign_keys=[requested_by_user_id])
    resolved_by_user = relationship("User", foreign_keys=[resolved_by_user_id])

    __table_args__ = (
        Index("ix_supply_request_branch_status", "branch_id", "status"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    SEVERITIES = ("baja", "media", "alta")
    STATUSES = ("abierta", "en_proceso", "resuelta")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    reported_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False, default="media")
    status = Column(String(20), nullable=False, default="abierta")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, nullable=True)

    branch = relationship("Branch")
    reported_by_user = relationship("User", foreign_keys=[reported_by_user_id])
    resolved_by_user = relationship("User", foreign_keys=[resolved_by_user_id])

    __table_args__ = (
        Index("ix_incident_branch_status", "branch_id", "status"),
    )


class Task(Base):
    __tablename__ = "tasks"

    STATUSES = ("pendiente", "en_proceso", "hecha", "cancelada")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pendiente")
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime, nullable=True)

    branch = relationship("Branch")
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    assigned_to_user = relationship("User", foreign_keys=[assigned_to_user_id])

    __table_args__ = (
        Index("ix_task_branch_status", "branch_id", "status"),
    )
