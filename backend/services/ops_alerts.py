"""
Avisos automáticos de operación (pedido 2026-10-02): que el encargado y el gerente no tengan
que estar revisando pantallas para enterarse de lo que se quedó sin hacer.

1. Tarea vencida: cuando una tarea pendiente pasa su hora límite, se avisa UNA vez, con el
   sonido de urgencia (canal "recordatorios"), a quien le toca (la persona asignada, o todo el
   equipo si es para la sucursal) y a quien la creó.
2. Cierre de turno que falta: 30 minutos después de la hora de cierre de la sucursal, si tiene
   hoja de cierre armada y nadie la llenó hoy, se avisa al equipo de esa sucursal. Una vez al día.
3. Resumen diario a las 8:00 a. m. para admin y gerentes (supervisores sin sucursal): cuántas
   sucursales cerraron turno ayer y cuáles no, tareas vencidas abiertas e insumos bajo mínimo.

Cada aviso deja un evento de auditoría, que sirve también para no repetirlo.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import or_

from database import SessionLocal
from models.audit import AuditEvent
from models.branch import Branch
from models.ops import Task
from models.stock_count import StockCount
from models.supply import ItemBranchSetting
from models.user import User
from services import fcm_service
from services.audit import log_audit_event
from services.branch_hours import PANAMA_TZ, branch_schedule
from services.push_service import notify_branch_staff, notify_users

logger = logging.getLogger("farmhouse.ops_alerts")

CHECK_INTERVAL_SECONDS = 10 * 60
STARTUP_DELAY_SECONDS = 300
OVERDUE_LOOKBACK_HOURS = 48      # una tarea vencida hace días ya no se "descubre" ahora
CLOSING_GRACE_MINUTES = 30
DIGEST_HOUR = 8


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _inicio_dia_utc(dia: date) -> datetime:
    """Medianoche de Panamá de ese día, en UTC sin zona (como se guardan las fechas)."""
    return datetime.combine(dia, datetime.min.time()) + timedelta(hours=5)


# --------------------------------------------------------------------------
# 1. Tareas vencidas
# --------------------------------------------------------------------------
def send_overdue_task_alerts(now: Optional[datetime] = None, db=None) -> int:
    from routers.ops import _branch_team_ids, _notify_task   # diferido: routers importa services
    propio = db is None
    db = db or SessionLocal()
    try:
        ahora = _utc_naive(now or datetime.now(timezone.utc))
        vencidas = (
            db.query(Task)
            .filter(Task.status.in_(["pendiente", "en_proceso"]), Task.due_date.isnot(None),
                    Task.due_date < ahora, Task.due_date >= ahora - timedelta(hours=OVERDUE_LOOKBACK_HOURS))
            .all()
        )
        if not vencidas:
            return 0
        avisadas = {
            eid for (eid,) in db.query(AuditEvent.entity_id)
            .filter(AuditEvent.action == "task.overdue_alert", AuditEvent.entity_id.in_([t.id for t in vencidas])).all()
        }
        enviadas = 0
        for t in vencidas:
            if t.id in avisadas:
                continue
            destinatarios = [t.assigned_to_user_id] if t.assigned_to_user_id else _branch_team_ids(db, t.branch_id)
            if t.created_by_user_id not in destinatarios:
                destinatarios = destinatarios + [t.created_by_user_id]
            _notify_task(db, destinatarios, f"Tarea vencida · {t.branch.name}", t.title, t, channel=fcm_service.CANAL_RECORDATORIOS)
            log_audit_event(db, None, t.branch_id, "task.overdue_alert", "task", t.id, {"title": t.title})
            enviadas += 1
        db.commit()
        if enviadas:
            logger.info(f"[OpsAlerts] Avisos de tarea vencida: {enviadas}")
        return enviadas
    finally:
        if propio:
            db.close()


# --------------------------------------------------------------------------
# 2. Cierre de turno que falta
# --------------------------------------------------------------------------
def _branches_with_sheet(db) -> List[Branch]:
    ids = {b for (b,) in db.query(ItemBranchSetting.branch_id).filter(ItemBranchSetting.on_closing_sheet == True).distinct().all()}  # noqa: E712
    if not ids:
        return []
    return db.query(Branch).filter(Branch.id.in_(ids), Branch.active == True, Branch.code != "CAT").order_by(Branch.name).all()  # noqa: E712


def _closed_between(db, branch_id: int, desde: datetime, hasta: datetime) -> bool:
    return db.query(StockCount.id).filter(
        StockCount.branch_id == branch_id, StockCount.kind == "closing",
        StockCount.counted_at >= desde, StockCount.counted_at < hasta,
    ).first() is not None


def send_missing_closing_alerts(now: Optional[datetime] = None, db=None) -> int:
    propio = db is None
    db = db or SessionLocal()
    try:
        ahora_pa = (now or datetime.now(PANAMA_TZ)).astimezone(PANAMA_TZ)
        hoy = ahora_pa.date()
        enviadas = 0
        for b in _branches_with_sheet(db):
            abre, cierra = branch_schedule(b)
            if cierra <= abre:
                continue   # horario que cruza la medianoche: no se avisa (no hay sucursales así hoy)
            limite = (datetime.combine(hoy, cierra) + timedelta(minutes=CLOSING_GRACE_MINUTES)).time()
            if ahora_pa.time() < limite:
                continue
            if _closed_between(db, b.id, _inicio_dia_utc(hoy), _inicio_dia_utc(hoy + timedelta(days=1))):
                continue
            ya = db.query(AuditEvent.id).filter(
                AuditEvent.action == "alert.closing_missing", AuditEvent.branch_id == b.id,
                AuditEvent.metadata_json.like(f'%"date": "{hoy.isoformat()}"%'),
            ).first()
            if ya:
                continue
            notify_branch_staff(db, b.id, f"Falta el cierre de turno · {b.name}",
                                "Nadie llenó la hoja de cierre hoy. Toca para hacerlo antes de irse.",
                                f"/consumo?branch={b.id}", tag=f"fh-cierre-{b.id}", channel=fcm_service.CANAL_TAREAS)
            log_audit_event(db, None, b.id, "alert.closing_missing", "branch", b.id, {"date": hoy.isoformat()})
            enviadas += 1
        db.commit()
        if enviadas:
            logger.info(f"[OpsAlerts] Avisos de cierre que falta: {enviadas}")
        return enviadas
    finally:
        if propio:
            db.close()


# --------------------------------------------------------------------------
# 3. Resumen diario para admin y gerentes
# --------------------------------------------------------------------------
def build_daily_digest(db, hoy: date) -> dict:
    from services.supply_alerts import low_stock_rows
    ayer = hoy - timedelta(days=1)
    con_hoja = _branches_with_sheet(db)
    sin_cierre = [b.name for b in con_hoja if not _closed_between(db, b.id, _inicio_dia_utc(ayer), _inicio_dia_utc(hoy))]
    ahora_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    vencidas = db.query(Task.id).filter(Task.status.in_(["pendiente", "en_proceso"]), Task.due_date.isnot(None), Task.due_date < ahora_utc).count()
    sucursales = db.query(Branch).filter(Branch.active == True, Branch.code != "CAT").all()  # noqa: E712
    bajo_minimo = 0
    for b in sucursales:
        try:
            bajo_minimo += len(low_stock_rows(db, b.id))
        except Exception:
            logger.warning("[OpsAlerts] No se pudo calcular stock bajo de %s", b.name, exc_info=True)
    partes = []
    if con_hoja:
        cerraron = len(con_hoja) - len(sin_cierre)
        txt = f"Ayer cerraron turno {cerraron} de {len(con_hoja)}"
        if sin_cierre:
            txt += f" (faltó {', '.join(sin_cierre[:3])}{'…' if len(sin_cierre) > 3 else ''})"
        partes.append(txt)
    partes.append(f"{vencidas} tarea{'' if vencidas == 1 else 's'} vencida{'' if vencidas == 1 else 's'}")
    partes.append(f"{bajo_minimo} insumo{'' if bajo_minimo == 1 else 's'} bajo mínimo")
    return {"body": ". ".join(partes) + ".", "sin_cierre": sin_cierre, "vencidas": vencidas, "bajo_minimo": bajo_minimo, "con_hoja": len(con_hoja)}


def send_daily_digest(now: Optional[datetime] = None, db=None) -> bool:
    propio = db is None
    db = db or SessionLocal()
    try:
        ahora_pa = (now or datetime.now(PANAMA_TZ)).astimezone(PANAMA_TZ)
        if ahora_pa.hour < DIGEST_HOUR:
            return False
        hoy = ahora_pa.date()
        ya = db.query(AuditEvent.id).filter(
            AuditEvent.action == "digest.daily", AuditEvent.metadata_json.like(f'%"date": "{hoy.isoformat()}"%'),
        ).first()
        if ya:
            return False
        resumen = build_daily_digest(db, hoy)
        gerencia = [u.id for u in db.query(User.id).filter(
            User.active == True, or_(User.role == "admin", (User.role == "supervisor") & User.branch_id.is_(None)),  # noqa: E712
        ).all()]
        notify_users(db, gerencia, "Resumen de hoy · Farmhouse", resumen["body"], "/gestion", tag="fh-digest-diario")
        log_audit_event(db, None, None, "digest.daily", "digest", None, {"date": hoy.isoformat(), "body": resumen["body"]})
        db.commit()
        logger.info(f"[OpsAlerts] Resumen diario a {len(gerencia)} persona(s): {resumen['body']}")
        return True
    finally:
        if propio:
            db.close()


async def run_ops_alerts_loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        for fn in (send_overdue_task_alerts, send_missing_closing_alerts, send_daily_digest):
            try:
                await asyncio.to_thread(fn)
            except Exception:
                logger.exception(f"[OpsAlerts] Falló {fn.__name__}.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
