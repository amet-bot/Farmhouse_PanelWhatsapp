"""
Resumen semanal por push a gerencia: los lunes a partir de las 8:00 (hora de Panamá) se manda
a admins y supervisores un aviso con la semana anterior (lunes a domingo): venta total y su
variación contra la semana previa, merma como porcentaje de la venta, compras, la sucursal
que más vendió e incidencias abiertas. Al tocarlo se abre Reportes.

Se manda una sola vez por semana: queda un evento de auditoría "digest.weekly" con el lunes de
esa semana, y antes de mandar se revisa que no exista. Corre en el mismo loop de fondo que el
resto (ver main.py), con la misma guarda de pytest.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from database import SessionLocal
from models.audit import AuditEvent
from models.ops import Incident
from models.user import User
from services.audit import log_audit_event
from services.branch_hours import PANAMA_TZ
from services.push_service import notify_users

logger = logging.getLogger("farmhouse.weekly_digest")

DIGEST_WEEKDAY = 0      # lunes
DIGEST_HOUR = 8         # a partir de las 8:00 de Panamá
CHECK_INTERVAL_SECONDS = 30 * 60
STARTUP_DELAY_SECONDS = 120


def week_bounds(today: date) -> tuple:
    """Lunes y domingo de la semana ANTERIOR a `today`."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def already_sent(db, monday: date) -> bool:
    for ev in db.query(AuditEvent).filter(AuditEvent.action == "digest.weekly").order_by(AuditEvent.id.desc()).limit(10).all():
        try:
            if (json.loads(ev.metadata_json or "{}").get("week") == monday.isoformat()):
                return True
        except ValueError:
            continue
    return False


def build_summary(db, monday: date, sunday: date) -> dict:
    """Cifras de la semana y de la previa, con el texto corto del aviso."""
    from routers.reports import month_figures   # import diferido: routers importa services

    actual = month_figures(db, monday, sunday, None)
    previa = month_figures(db, monday - timedelta(days=7), sunday - timedelta(days=7), None)
    ventas = actual["total"]["sales_net"]
    ventas_prev = previa["total"]["sales_net"]
    variacion = None
    if ventas_prev:
        variacion = round((float(ventas) - float(ventas_prev)) / float(ventas_prev) * 100, 1)
    mejor = actual["branches"][0] if actual["branches"] and actual["branches"][0]["sales_net"] else None
    abiertas = db.query(Incident).filter(Incident.status != "resuelta").count()

    partes = [f"Ventas ${ventas:,.0f}" + (f" ({'+' if variacion >= 0 else ''}{variacion}% vs semana previa)" if variacion is not None else "")]
    if actual["total"]["waste_pct_sales"] is not None:
        partes.append(f"merma {actual['total']['waste_pct_sales']}% de la venta")
    if actual["total"]["purchases"]:
        partes.append(f"compras ${actual['total']['purchases']:,.0f}")
    if mejor:
        partes.append(f"mejor: {mejor['branch_name']} ${mejor['sales_net']:,.0f}")
    if abiertas:
        partes.append(f"{abiertas} incidencia{'s' if abiertas != 1 else ''} abierta{'s' if abiertas != 1 else ''}")
    return {
        "week": monday.isoformat(), "sales_net": ventas, "sales_prev": ventas_prev, "variation_pct": variacion,
        "waste_pct": actual["total"]["waste_pct_sales"], "purchases": actual["total"]["purchases"],
        "best_branch": mejor["branch_name"] if mejor else None, "open_incidents": abiertas,
        "title": f"Resumen semanal · {monday.strftime('%d/%m')} al {sunday.strftime('%d/%m')}",
        "body": " · ".join(partes),
    }


def send_if_due(now: Optional[datetime] = None, db=None) -> bool:
    """Manda el resumen si es lunes después de las 8:00 y todavía no salió esta semana."""
    now = now or datetime.now(PANAMA_TZ)
    if now.weekday() != DIGEST_WEEKDAY or now.hour < DIGEST_HOUR:
        return False
    propia = db is None
    db = db or SessionLocal()
    try:
        monday, sunday = week_bounds(now.date())
        if already_sent(db, monday):
            return False
        resumen = build_summary(db, monday, sunday)
        destinatarios = [u.id for u in db.query(User.id).filter(User.active == True, User.role.in_(["admin", "supervisor"])).all()]  # noqa: E712
        enviados = notify_users(db, destinatarios, resumen["title"], resumen["body"], f"/link?date_from={monday}&date_to={sunday}", tag=f"fh-digest-{monday}")
        log_audit_event(db, None, None, "digest.weekly", "report", None, {"week": monday.isoformat(), "recipients": len(destinatarios), "sent": enviados, "body": resumen["body"]})
        db.commit()
        logger.info(f"[Digest] Resumen semanal {monday} enviado a {enviados} dispositivo(s).")
        return True
    finally:
        if propia:
            db.close()


async def run_weekly_digest_loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(send_if_due)
        except Exception:
            logger.exception("[Digest] Error al revisar el resumen semanal.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
