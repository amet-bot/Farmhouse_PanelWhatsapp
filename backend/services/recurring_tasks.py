"""
Tareas recurrentes: cuando toca la hora (o el día del mes) de una RecurringTaskTemplate activa
(ver models/ops.py y routers/ops.py: create_recurring_task), crea una Task real SIN ASIGNAR —
le llega a todo el equipo de la sucursal, igual que una tarea manual sin asignar — y avisa por
push. Mismo patrón de loop que services/supply_alerts.py: revisa cada CHECK_INTERVAL_SECONDS y
usa la auditoría para no repetir el mismo disparo dos veces (un reinicio del servidor a media
mañana no debe duplicar la tarea de las 8am; "ya pasó la hora" se sigue cumpliendo igual).
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from database import SessionLocal
from models.audit import AuditEvent
from models.branch import Branch
from models.ops import RecurringTaskTemplate, Task
from services.audit import log_audit_event
from services.branch_hours import PANAMA_TZ

logger = logging.getLogger("farmhouse.recurring_tasks")

CHECK_INTERVAL_SECONDS = 5 * 60
STARTUP_DELAY_SECONDS = 90


def _target_branches(db, template: RecurringTaskTemplate) -> List[Branch]:
    if template.branch_id is not None:
        b = db.query(Branch).filter(Branch.id == template.branch_id, Branch.active == True).first()  # noqa: E712
        return [b] if b else []
    # "Cada local": todas las sucursales activas menos Catering (no tiene equipo de línea/producción).
    return db.query(Branch).filter(Branch.active == True, Branch.code != "CAT").all()  # noqa: E712


def _day_for_month(day_of_month: int, now: datetime) -> int:
    """El día `day_of_month` de este mes, o el último día si el mes es más corto (ej. "el 30"
    en febrero cae el 28 o 29)."""
    next_month = now.replace(day=28) + timedelta(days=4)
    last_day = (next_month.replace(day=1) - timedelta(days=1)).day
    return min(day_of_month, last_day)


def _already_fired(db, template_id: int, branch_id: int, slot: str, day: str) -> bool:
    for ev in db.query(AuditEvent).filter(
        AuditEvent.action == "recurring_task.fire",
        AuditEvent.entity_type == "recurring_task_template",
        AuditEvent.entity_id == template_id,
        AuditEvent.branch_id == branch_id,
    ).order_by(AuditEvent.id.desc()).limit(15).all():
        try:
            meta = json.loads(ev.metadata_json or "{}")
        except ValueError:
            continue
        if meta.get("slot") == slot and meta.get("date") == day:
            return True
    return False


def _end_of_day_utc(now_panama: datetime) -> datetime:
    """"Vence hoy a las 11:59pm" en hora de Panamá, como UTC sin zona (igual que el resto de
    columnas DATETIME del sistema)."""
    fin = now_panama.replace(hour=23, minute=59, second=0, microsecond=0)
    return fin.astimezone(timezone.utc).replace(tzinfo=None)


def check_and_fire(now: Optional[datetime] = None, db=None) -> int:
    """Crea las Tasks que correspondan a esta hora/día. Devuelve cuántas creó."""
    # Import diferido: routers importa services (mismo patrón que supply_alerts con routers.inventory).
    from routers.ops import _notify_task, _branch_team_ids

    now = now or datetime.now(PANAMA_TZ)
    propia = db is None
    db = db or SessionLocal()
    creadas = 0
    try:
        hoy = now.date().isoformat()
        hora_actual = now.strftime("%H:%M")
        due = _end_of_day_utc(now)
        for template in db.query(RecurringTaskTemplate).filter(RecurringTaskTemplate.active == True).all():  # noqa: E712
            if template.frequency == "monthly":
                if not template.day_of_month or now.day != _day_for_month(template.day_of_month, now):
                    continue
            try:
                times = json.loads(template.times_json or "[]")
            except ValueError:
                times = []
            # Las horas que ya pasaron hoy (no "es exactamente esta hora"): si el servidor estuvo
            # caído justo a las 8am, la tarea de las 8am igual sale apenas vuelve a arrancar.
            slots_due = [t for t in times if t <= hora_actual]
            if not slots_due:
                continue
            for branch in _target_branches(db, template):
                for slot in slots_due:
                    if _already_fired(db, template.id, branch.id, slot, hoy):
                        continue
                    task = Task(
                        branch_id=branch.id, created_by_user_id=template.created_by_user_id,
                        assigned_to_user_id=None, title=template.title, description=template.description,
                        due_date=due,
                    )
                    db.add(task)
                    db.flush()
                    log_audit_event(
                        db, template.created_by_user_id, branch.id, "recurring_task.fire",
                        "recurring_task_template", template.id, {"date": hoy, "slot": slot, "task_id": task.id},
                    )
                    db.commit()
                    db.refresh(task)
                    logger.info(f"[RecurringTasks] Tarea #{task.id} ({template.title}) creada en sucursal {branch.id} para las {slot}.")
                    try:
                        _notify_task(db, _branch_team_ids(db, branch.id), f"Tarea para {branch.name}", task.title, task)
                    except Exception:
                        logger.warning("[RecurringTasks] No se pudo avisar por push.", exc_info=True)
                    creadas += 1
        return creadas
    finally:
        if propia:
            db.close()


async def run_recurring_tasks_loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(check_and_fire)
        except Exception:
            logger.exception("[RecurringTasks] Error al revisar las tareas recurrentes.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
