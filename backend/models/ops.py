from sqlalchemy import Column, Integer, Numeric, String, DateTime, ForeignKey, Text, Index, Boolean
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
- RecurringTaskTemplate ("tarea recurrente"): la regla que genera Tasks solas, a hora(s) fija(s)
  cada día o un día fijo del mes (ver services/recurring_tasks.py).
"""


class SupplyRequest(Base):
    __tablename__ = "supply_requests"

    # open -> approved (un encargado la aprueba) -> fulfilled (llegó); cancelled desde open/approved.
    # "approved" es opcional: un encargado puede marcarla entregada directo.
    STATUSES = ("open", "approved", "fulfilled", "cancelled")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_name = Column(String(150), nullable=False)
    quantity_hint = Column(String(50), nullable=True)
    # Opcionales (bloque de abastecimiento): si la solicitud se ligó a un insumo del catálogo con
    # una cantidad, el pedido sugerido la cuenta. El texto libre sigue valiendo para lo demás.
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True)
    quantity = Column(Numeric(10, 3), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    branch = relationship("Branch")
    requested_by_user = relationship("User", foreign_keys=[requested_by_user_id])
    approved_by_user = relationship("User", foreign_keys=[approved_by_user_id])
    resolved_by_user = relationship("User", foreign_keys=[resolved_by_user_id])
    inventory_item = relationship("InventoryItem")

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
    # Quién la tiene a cargo (encargado de la sucursal, mantenimiento, logística...). Opcional.
    assigned_to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, nullable=True)

    branch = relationship("Branch")
    reported_by_user = relationship("User", foreign_keys=[reported_by_user_id])
    assigned_to_user = relationship("User", foreign_keys=[assigned_to_user_id])
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


class RecurringTaskTemplate(Base):
    """La regla que crea Tasks solas (ver services/recurring_tasks.py), para encargos que se
    repiten siempre igual: limpieza de apertura, listas de pares de producción a ciertas horas,
    el inventario de fin de mes. Cuando toca, crea una Task real SIN ASIGNAR (le llega a todo el
    equipo de la sucursal, igual que una tarea manual sin asignar) — nunca edita ni reusa la
    tarea anterior: cada ocurrencia es una Task nueva e independiente, con su propio historial.

    `branch_id` None es "cada local": se crea en todas las sucursales activas (menos Catering,
    que no tiene equipo de línea/producción que la vea en /tareas).
    `frequency="daily"` dispara una vez por cada hora en `times_json` (ej. ["07:00"], o varias:
    ["08:00","15:00","20:00"]). `frequency="monthly"` dispara una vez al mes en `day_of_month`
    (si ese mes es más corto, cae el último día) a la primera hora de `times_json`.
    """
    __tablename__ = "recurring_task_templates"

    FREQUENCIES = ("daily", "monthly")

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # None = todas las sucursales activas; con sucursal, solo esa.
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    frequency = Column(String(20), nullable=False, default="daily")
    # JSON de horas "HH:MM" en hora de Panamá, ej. '["07:00"]' o '["08:00","15:00","20:00"]'.
    times_json = Column(Text, nullable=False)
    # Solo con frequency="monthly": día del mes (1-31).
    day_of_month = Column(Integer, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    branch = relationship("Branch")
    created_by_user = relationship("User")

    __table_args__ = (
        Index("ix_recurring_task_branch_active", "branch_id", "active"),
    )
