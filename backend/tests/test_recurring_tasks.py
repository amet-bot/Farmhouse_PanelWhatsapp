"""
Tareas recurrentes: la regla (RecurringTaskTemplate) vía API — permisos, validación, pausar/
reanudar/eliminar — y el disparador (services.recurring_tasks.check_and_fire) que crea la Task
real a su hora, una sola vez por slot y día, en una sucursal o en todas ("cada local").
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from models.audit import AuditEvent
from models.branch import Branch
from models.ops import RecurringTaskTemplate, Task
from services import recurring_tasks
from tests.conftest import auth_headers_for
from tests.test_ops_center import avisos  # noqa: F401 (fixture reusada)

PANAMA_TZ = ZoneInfo("America/Panama")


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


# ---- API: crear / listar / permisos --------------------------------------------------------

def test_crear_tarea_recurrente_diaria_y_listarla(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "Limpieza de la mañana",
        "description": "Área de la línea", "frequency": "daily", "times": ["07:00"],
    }, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["branch_name"] == "Clayton" and body["times"] == ["07:00"] and body["active"] is True
    assert body["frequency"] == "daily" and body["day_of_month"] is None

    rows = client.get("/api/ops/recurring-tasks", headers=h).json()
    assert len(rows) == 1 and rows[0]["title"] == "Limpieza de la mañana"


def test_varias_horas_se_ordenan_y_sin_duplicados(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "Listas de pares de producción",
        "frequency": "daily", "times": ["20:00", "08:00", "08:00", "15:00"],
    }, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["times"] == ["08:00", "15:00", "20:00"]


def test_agente_no_puede_crear_tarea_recurrente(client, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "x", "frequency": "daily", "times": ["07:00"],
    }, headers=h)
    assert r.status_code == 403


def test_cada_local_requiere_admin_global(client, clayton_branch, supervisor_user, admin_user, clayton_device):
    # Supervisor local (de Clayton): no puede crear una regla para "todas las sucursales".
    h_local = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": None, "title": "Entregar inventarios", "frequency": "monthly",
        "times": ["09:00"], "day_of_month": 30,
    }, headers=h_local)
    assert r.status_code == 403

    # Admin (global): sí puede.
    h_admin = _h(admin_user)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": None, "title": "Entregar inventarios", "frequency": "monthly",
        "times": ["09:00"], "day_of_month": 30,
    }, headers=h_admin)
    assert r.status_code == 201, r.text
    assert r.json()["branch_id"] is None and r.json()["branch_name"] is None


def test_hora_invalida_se_rechaza(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "x", "frequency": "daily", "times": ["7am"],
    }, headers=h)
    assert r.status_code == 400


def test_mensual_sin_dia_se_rechaza(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "x", "frequency": "monthly", "times": ["09:00"],
    }, headers=h)
    assert r.status_code == 400


def test_pausar_reanudar_y_eliminar(client, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    created = client.post("/api/ops/recurring-tasks", json={
        "branch_id": clayton_branch.id, "title": "x", "frequency": "daily", "times": ["07:00"],
    }, headers=h).json()

    r = client.patch(f"/api/ops/recurring-tasks/{created['id']}", json={"active": False}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is False

    r = client.patch(f"/api/ops/recurring-tasks/{created['id']}", json={"active": True, "times": ["06:30"]}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is True and r.json()["times"] == ["06:30"]

    assert client.delete(f"/api/ops/recurring-tasks/{created['id']}", headers=h).status_code == 204
    assert client.get("/api/ops/recurring-tasks", headers=h).json() == []


# ---- disparador: services.recurring_tasks.check_and_fire -----------------------------------

def _plantilla(db_session, **kw):
    import json
    defaults = dict(created_by_user_id=kw.pop("created_by_user_id"), title="Tarea de prueba",
                     frequency="daily", times_json=json.dumps(kw.pop("times", ["07:00"])), active=True)
    defaults.update(kw)
    t = RecurringTaskTemplate(**defaults)
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def test_dispara_una_vez_por_dia_aunque_se_revise_varias_veces(client, db_session, clayton_branch, supervisor_user, clayton_agent, avisos):
    _plantilla(db_session, created_by_user_id=supervisor_user.id, branch_id=clayton_branch.id, times=["07:00"])
    now = datetime(2026, 10, 5, 7, 5, tzinfo=PANAMA_TZ)   # ya pasaron las 7:00

    assert recurring_tasks.check_and_fire(now=now, db=db_session) == 1
    assert recurring_tasks.check_and_fire(now=now, db=db_session) == 0   # ya se disparó hoy
    assert recurring_tasks.check_and_fire(now=now.replace(hour=12), db=db_session) == 0   # más tarde, mismo día

    tasks = db_session.query(Task).filter(Task.branch_id == clayton_branch.id).all()
    assert len(tasks) == 1 and tasks[0].assigned_to_user_id is None and tasks[0].title == "Tarea de prueba"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "recurring_task.fire").count() == 1
    # Le llegó al equipo de Clayton (sin asignar == para todos).
    assert any(a["kind"] == "users" and clayton_agent.id in a["user_ids"] for a in avisos)


def test_varias_horas_el_mismo_dia_disparan_cada_una_una_vez(db_session, clayton_branch, supervisor_user, avisos):
    _plantilla(db_session, created_by_user_id=supervisor_user.id, branch_id=clayton_branch.id, times=["08:00", "15:00", "20:00"])

    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 8, 10, tzinfo=PANAMA_TZ), db=db_session) == 1
    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 8, 10, tzinfo=PANAMA_TZ), db=db_session) == 0
    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 15, 5, tzinfo=PANAMA_TZ), db=db_session) == 1
    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 20, 1, tzinfo=PANAMA_TZ), db=db_session) == 1
    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 23, 59, tzinfo=PANAMA_TZ), db=db_session) == 0

    assert db_session.query(Task).filter(Task.branch_id == clayton_branch.id).count() == 3


def test_tarea_pausada_no_dispara(db_session, clayton_branch, supervisor_user):
    t = _plantilla(db_session, created_by_user_id=supervisor_user.id, branch_id=clayton_branch.id, times=["07:00"])
    t.active = False
    db_session.commit()
    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 7, 5, tzinfo=PANAMA_TZ), db=db_session) == 0
    assert db_session.query(Task).count() == 0


def test_cada_local_crea_en_todas_menos_catering(db_session, clayton_branch, obarrio_branch, admin_user):
    cat = Branch(id=99, code="CAT", name="Catering", color="#000000", active=True)
    db_session.add(cat)
    db_session.commit()
    _plantilla(db_session, created_by_user_id=admin_user.id, branch_id=None, times=["07:00"])

    assert recurring_tasks.check_and_fire(now=datetime(2026, 10, 5, 7, 5, tzinfo=PANAMA_TZ), db=db_session) == 2
    branches_con_tarea = {t.branch_id for t in db_session.query(Task).all()}
    assert branches_con_tarea == {clayton_branch.id, obarrio_branch.id}


def test_mensual_en_mes_corto_cae_el_ultimo_dia(db_session, clayton_branch, supervisor_user):
    _plantilla(db_session, created_by_user_id=supervisor_user.id, branch_id=clayton_branch.id,
               times=["09:00"], frequency="monthly", day_of_month=30)

    # 2026 no es bisiesto: febrero tiene 28 días. El día 27 todavía no toca.
    assert recurring_tasks.check_and_fire(now=datetime(2026, 2, 27, 9, 5, tzinfo=PANAMA_TZ), db=db_session) == 0
    assert recurring_tasks.check_and_fire(now=datetime(2026, 2, 28, 9, 5, tzinfo=PANAMA_TZ), db=db_session) == 1
    # Y en un mes de 30/31 días, dispara justo el día pedido.
    assert recurring_tasks.check_and_fire(now=datetime(2026, 3, 30, 9, 5, tzinfo=PANAMA_TZ), db=db_session) == 1
    assert recurring_tasks.check_and_fire(now=datetime(2026, 3, 31, 9, 5, tzinfo=PANAMA_TZ), db=db_session) == 0
