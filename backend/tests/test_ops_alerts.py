"""
Avisos automáticos: tarea vencida (una vez, sonido de urgencia), cierre de turno que falta (30
min después del cierre, una vez al día) y resumen diario a gerencia a las 8:00.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from models.audit import AuditEvent
from models.inventory_item import InventoryItem
from models.ops import Task
from models.stock_count import StockCount
from models.supply import ItemBranchSetting
from models.user import User
from security.auth import get_password_hash
from services import fcm_service, ops_alerts
from services.branch_hours import PANAMA_TZ


@pytest.fixture
def avisos(monkeypatch):
    enviados = []
    monkeypatch.setattr("routers.ops.notify_users", lambda db, ids, title, body, url, **kw: enviados.append({"kind": "users", "ids": list(ids), "title": title, "body": body, "url": url, **kw}) or 1)
    monkeypatch.setattr(ops_alerts, "notify_users", lambda db, ids, title, body, url, **kw: enviados.append({"kind": "digest", "ids": list(ids), "title": title, "body": body, "url": url, **kw}) or 1)
    monkeypatch.setattr(ops_alerts, "notify_branch_staff", lambda db, branch_id, title, body, url, **kw: enviados.append({"kind": "branch", "branch_id": branch_id, "title": title, "url": url, **kw}) or 1)
    return enviados


def test_tarea_vencida_avisa_una_vez_con_sonido_de_urgencia(db_session, admin_user, clayton_branch, clayton_agent, supervisor_user, avisos):
    ahora = datetime.utcnow()
    vencida = Task(branch_id=clayton_branch.id, created_by_user_id=admin_user.id, title="Limpiar campana", due_date=ahora - timedelta(hours=1))
    a_tiempo = Task(branch_id=clayton_branch.id, created_by_user_id=admin_user.id, title="Sacar basura", due_date=ahora + timedelta(hours=2))
    vieja = Task(branch_id=clayton_branch.id, created_by_user_id=admin_user.id, title="De hace días", due_date=ahora - timedelta(days=5))
    hecha = Task(branch_id=clayton_branch.id, created_by_user_id=admin_user.id, title="Ya hecha", due_date=ahora - timedelta(hours=1), status="hecha")
    db_session.add_all([vencida, a_tiempo, vieja, hecha]); db_session.commit()

    assert ops_alerts.send_overdue_task_alerts(db=db_session) == 1
    avisados = {i for a in avisos for i in a["ids"]}
    # Sin asignar: todo el equipo de la sucursal + quien la creó.
    assert avisados == {clayton_agent.id, supervisor_user.id, admin_user.id}
    assert all(a["channel"] == fcm_service.CANAL_RECORDATORIOS and a["title"].startswith("Tarea vencida") for a in avisos)
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "task.overdue_alert", AuditEvent.entity_id == vencida.id).count() == 1

    avisos.clear()
    assert ops_alerts.send_overdue_task_alerts(db=db_session) == 0 and avisos == []   # no se repite


def _hoja(db, branch, item):
    db.add(ItemBranchSetting(inventory_item_id=item.id, branch_id=branch.id, on_closing_sheet=True, sheet_position=1))
    db.commit()


def test_cierre_que_falta_avisa_despues_de_la_hora_y_una_vez(db_session, admin_user, clayton_branch, obarrio_branch, avisos):
    pollo = InventoryItem(name="Pollo", unit="kilogramo")
    db_session.add(pollo); db_session.commit()
    _hoja(db_session, clayton_branch, pollo)          # Obarrio no tiene hoja: nunca se le avisa
    clayton_branch.closes_at = "21:30"
    db_session.commit()

    antes = datetime(2026, 10, 3, 21, 45, tzinfo=PANAMA_TZ)
    despues = datetime(2026, 10, 3, 22, 5, tzinfo=PANAMA_TZ)
    assert ops_alerts.send_missing_closing_alerts(now=antes, db=db_session) == 0       # todavía no pasan los 30 min
    assert ops_alerts.send_missing_closing_alerts(now=despues, db=db_session) == 1
    assert avisos[-1]["branch_id"] == clayton_branch.id and avisos[-1]["url"] == f"/consumo?branch={clayton_branch.id}"
    assert ops_alerts.send_missing_closing_alerts(now=despues + timedelta(minutes=20), db=db_session) == 0   # una vez al día

    # Al día siguiente, si cerraron turno, no hay aviso.
    otro_dia = datetime(2026, 10, 4, 22, 5, tzinfo=PANAMA_TZ)
    db_session.add(StockCount(branch_id=clayton_branch.id, counted_by_user_id=admin_user.id, kind="closing",
                              counted_at=otro_dia.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=40)))
    db_session.commit()
    assert ops_alerts.send_missing_closing_alerts(now=otro_dia, db=db_session) == 0


def test_resumen_diario_a_gerencia(db_session, admin_user, clayton_branch, obarrio_branch, supervisor_user, avisos):
    gerente = User(username="g.log", name="Gerente", email="g@f.pa", password_hash=get_password_hash("x1234567"), role="supervisor", branch_id=None, active=True)
    pollo = InventoryItem(name="Pollo", unit="kilogramo")
    db_session.add_all([gerente, pollo]); db_session.commit()
    _hoja(db_session, clayton_branch, pollo)
    _hoja(db_session, obarrio_branch, pollo)
    hoy = datetime(2026, 10, 3, 8, 10, tzinfo=PANAMA_TZ)
    ayer_noche = datetime(2026, 10, 2, 21, 50, tzinfo=PANAMA_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    db_session.add(StockCount(branch_id=clayton_branch.id, counted_by_user_id=admin_user.id, kind="closing", counted_at=ayer_noche))
    db_session.add(Task(branch_id=clayton_branch.id, created_by_user_id=admin_user.id, title="x", due_date=datetime.utcnow() - timedelta(hours=3)))
    db_session.commit()

    assert ops_alerts.send_daily_digest(now=hoy.replace(hour=7), db=db_session) is False   # antes de las 8
    assert ops_alerts.send_daily_digest(now=hoy, db=db_session) is True
    d = avisos[-1]
    assert d["kind"] == "digest" and set(d["ids"]) == {admin_user.id, gerente.id}        # el encargado local no
    assert f"cerraron turno 1 de 2 (faltó {obarrio_branch.name})" in d["body"] and "1 tarea vencida" in d["body"]
    assert ops_alerts.send_daily_digest(now=hoy + timedelta(hours=2), db=db_session) is False   # una vez al día
