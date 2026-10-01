"""
Centro de operación multisucursal: resumen por sucursal, incidencias asignables con aviso,
solicitudes con aprobación de encargados, tareas vencidas, avisos de traslados y auditoría.
"""
from datetime import datetime, timedelta, date

import pytest

from models.audit import AuditEvent
from models.ops import Incident, SupplyRequest, Task
from models.shipment import ExpectedShipment
from models.stock_count import StockCount
from tests.conftest import auth_headers_for


@pytest.fixture
def avisos(monkeypatch):
    """Captura los push en vez de mandarlos."""
    enviados = []
    monkeypatch.setattr("routers.ops.notify_branch_staff", lambda db, branch_id, title, body, url, **kw: enviados.append({"kind": "branch", "branch_id": branch_id, "title": title, "body": body, "url": url, **kw}) or 1)
    monkeypatch.setattr("routers.ops.notify_users", lambda db, user_ids, title, body, url, **kw: enviados.append({"kind": "users", "user_ids": list(user_ids), "title": title, "body": body, "url": url, **kw}) or 1)
    monkeypatch.setattr("routers.transfers.notify_branch_staff", lambda db, branch_id, title, body, url, **kw: enviados.append({"kind": "branch", "branch_id": branch_id, "title": title, "body": body, "url": url, **kw}) or 1)
    return enviados


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


# ---- incidencias -------------------------------------------------------------

def test_incidencia_grave_avisa_a_encargados_y_se_puede_asignar(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, avisos):
    h = _h(clayton_agent, clayton_device)
    r = client.post("/api/ops/incidents", json={"branch_id": clayton_branch.id, "title": "Se dañó el refrigerador", "severity": "alta"}, headers=h)
    assert r.status_code == 201, r.text
    inc = r.json()
    assert avisos[-1]["kind"] == "branch" and avisos[-1]["managers_only"] is True and "GRAVE" in avisos[-1]["title"]
    assert avisos[-1]["url"].startswith("/gestion?tab=incidencias")

    hs = _h(supervisor_user, clayton_device)
    r = client.post(f"/api/ops/incidents/{inc['id']}/assign", json={"user_id": supervisor_user.id}, headers=hs)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to_name"] == supervisor_user.name and r.json()["status"] == "en_proceso"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "incident.assign").count() == 1


def test_reabrir_una_incidencia_limpia_la_resolucion(client, db_session, clayton_branch, supervisor_user, clayton_device, avisos):
    h = _h(supervisor_user, clayton_device)
    inc = client.post("/api/ops/incidents", json={"branch_id": clayton_branch.id, "title": "Gotera", "severity": "baja"}, headers=h).json()
    r = client.post(f"/api/ops/incidents/{inc['id']}/status", json={"status": "resuelta", "resolution_notes": "Se selló."}, headers=h).json()
    assert r["resolved_at"] is not None and r["resolved_by_name"] == supervisor_user.name and r["hours_open"] is None
    r = client.post(f"/api/ops/incidents/{inc['id']}/status", json={"status": "abierta"}, headers=h).json()
    assert r["resolved_at"] is None and r["resolved_by_user_id"] is None and r["hours_open"] is not None


def test_no_se_puede_asignar_a_alguien_de_otra_sucursal(client, db_session, clayton_branch, obarrio_branch, obarrio_agent, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    inc = client.post("/api/ops/incidents", json={"branch_id": clayton_branch.id, "title": "Luz fundida"}, headers=h).json()
    assert client.post(f"/api/ops/incidents/{inc['id']}/assign", json={"user_id": obarrio_agent.id}, headers=h).status_code == 400


# ---- solicitudes ---------------------------------------------------------------

def test_solo_un_encargado_aprueba_o_entrega_una_solicitud(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, avisos):
    ha = _h(clayton_agent, clayton_device)
    req = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "Aguacate", "quantity_hint": "2 cajas"}, headers=ha).json()
    assert avisos[-1]["managers_only"] is True and "Aguacate" in avisos[-1]["body"]

    assert client.post(f"/api/ops/requests/{req['id']}/status?status=approved", headers=ha).status_code == 403
    assert client.post(f"/api/ops/requests/{req['id']}/status?status=fulfilled", headers=ha).status_code == 403

    hs = _h(supervisor_user, clayton_device)
    r = client.post(f"/api/ops/requests/{req['id']}/status?status=approved", headers=hs)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["approved_by_name"] == supervisor_user.name
    assert avisos[-1]["kind"] == "users" and avisos[-1]["user_ids"] == [clayton_agent.id] and "aprobada" in avisos[-1]["title"]

    r = client.post(f"/api/ops/requests/{req['id']}/status?status=fulfilled", headers=hs).json()
    assert r["status"] == "fulfilled" and r["resolved_by_name"] == supervisor_user.name
    assert client.post(f"/api/ops/requests/{req['id']}/status?status=cancelled", headers=hs).status_code == 409
    acciones = {a.action for a in db_session.query(AuditEvent).all()}
    assert {"supply_request.create", "supply_request.approved", "supply_request.fulfilled"} <= acciones


def test_quien_pidio_puede_cancelar_su_solicitud(client, clayton_branch, clayton_agent, clayton_device, avisos):
    ha = _h(clayton_agent, clayton_device)
    req = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "Limones"}, headers=ha).json()
    assert client.post(f"/api/ops/requests/{req['id']}/status?status=cancelled", headers=ha).json()["status"] == "cancelled"


def test_listado_de_pendientes_incluye_abiertas_y_aprobadas(client, clayton_branch, supervisor_user, clayton_device, avisos):
    hs = _h(supervisor_user, clayton_device)
    a = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "A"}, headers=hs).json()
    b = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "B"}, headers=hs).json()
    client.post(f"/api/ops/requests/{b['id']}/status?status=approved", headers=hs)
    c = client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "C"}, headers=hs).json()
    client.post(f"/api/ops/requests/{c['id']}/status?status=fulfilled", headers=hs)
    ids = {r["id"] for r in client.get("/api/ops/requests?status=pendientes", headers=hs).json()}
    assert ids == {a["id"], b["id"]}


# ---- tareas --------------------------------------------------------------------

def test_tarea_vencida_asignacion_y_aviso(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, avisos):
    hs = _h(supervisor_user, clayton_device)
    vencida = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    r = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Limpiar campana", "due_date": vencida, "assigned_to_user_id": clayton_agent.id}, headers=hs)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["overdue"] is True and t["assigned_to_name"] == clayton_agent.name
    assert avisos[-1]["kind"] == "users" and avisos[-1]["user_ids"] == [clayton_agent.id]

    assert [x["id"] for x in client.get("/api/ops/tasks?overdue=true", headers=hs).json()] == [t["id"]]

    manana = (datetime.utcnow() + timedelta(days=1)).isoformat()
    r = client.patch(f"/api/ops/tasks/{t['id']}", json={"due_date": manana, "title": "Limpiar campana extractora"}, headers=hs).json()
    assert r["overdue"] is False and r["title"] == "Limpiar campana extractora"

    ha = _h(clayton_agent, clayton_device)
    r = client.post(f"/api/ops/tasks/{t['id']}/status", json={"status": "hecha"}, headers=ha).json()
    assert r["status"] == "hecha" and r["completed_at"] is not None
    assert avisos[-1]["kind"] == "users" and avisos[-1]["user_ids"] == [supervisor_user.id] and "Tarea hecha" in avisos[-1]["title"]
    assert client.patch(f"/api/ops/tasks/{t['id']}", json={"title": "x"}, headers=hs).status_code == 409


def test_tarea_sin_asignar_le_llega_a_todo_el_equipo_de_la_sucursal(client, db_session, admin_user, clayton_branch, obarrio_branch, clayton_agent, obarrio_agent, clayton_device, supervisor_user, inactive_user, avisos):
    """El dueño manda una tarea a Clayton sin asignarla: le llega al equipo de Clayton (cada uno
    con el enlace que puede abrir), no a Obarrio ni a quien la creó."""
    ha = _h(admin_user)
    r = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Limpiar la campana"}, headers=ha)
    assert r.status_code == 201, r.text
    t = r.json()
    por_url = {a["url"]: a for a in avisos if a["kind"] == "users"}
    assert por_url[f"/tareas?task={t['id']}"]["user_ids"] == [clayton_agent.id]
    assert por_url[f"/gestion?tab=tareas&branch={clayton_branch.id}"]["user_ids"] == [supervisor_user.id]
    avisados = {uid for a in avisos for uid in a.get("user_ids", [])}
    assert obarrio_agent.id not in avisados and admin_user.id not in avisados and inactive_user.id not in avisados
    assert all(a["title"] == f"Tarea para {clayton_branch.name}" and a["tag"] == f"fh-task-{t['id']}" for a in avisos)

    # Quien la crea desde la sucursal no se avisa a sí mismo.
    avisos.clear()
    client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Sacar la basura"}, headers=_h(supervisor_user, clayton_device))
    assert [a["user_ids"] for a in avisos] == [[clayton_agent.id]]

    # El empleado la ve en su lista y la marca hecha; el aviso al dueño lo lleva al Centro de operación.
    hc = _h(clayton_agent, clayton_device)
    assert t["id"] in [x["id"] for x in client.get("/api/ops/tasks?status=pendientes", headers=hc).json()]
    avisos.clear()
    assert client.post(f"/api/ops/tasks/{t['id']}/status", json={"status": "hecha"}, headers=hc).status_code == 200
    assert avisos[-1]["user_ids"] == [admin_user.id] and avisos[-1]["url"].startswith("/gestion?tab=tareas")


def test_no_se_asigna_a_un_usuario_inactivo(client, clayton_branch, supervisor_user, clayton_device, inactive_user, avisos):
    hs = _h(supervisor_user, clayton_device)
    r = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "x", "assigned_to_user_id": inactive_user.id}, headers=hs)
    assert r.status_code == 404


def test_borrar_una_tarea_es_solo_de_admin(client, db_session, clayton_branch, supervisor_user, admin_user, clayton_device, avisos):
    """Borrar de verdad (no solo cancelar) queda reservado a admin: un encargado local, aunque
    tenga purchasing.approve, no puede quitar el único registro de si una tarea se hizo."""
    hs = _h(supervisor_user, clayton_device)
    t = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Tarea de prueba"}, headers=hs).json()

    assert client.delete(f"/api/ops/tasks/{t['id']}", headers=hs).status_code == 403
    assert db_session.query(Task).filter(Task.id == t["id"]).first() is not None

    ha = _h(admin_user)
    assert client.delete(f"/api/ops/tasks/{t['id']}", headers=ha).status_code == 204
    assert db_session.query(Task).filter(Task.id == t["id"]).first() is None
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "task.delete", AuditEvent.entity_id == t["id"]).count() == 1

    assert client.delete(f"/api/ops/tasks/{t['id']}", headers=ha).status_code == 404


def test_recordar_una_tarea_avisa_de_nuevo_a_quien_le_toca(client, db_session, admin_user, clayton_branch, obarrio_branch, clayton_agent, obarrio_agent, supervisor_user, clayton_device, avisos):
    ha = _h(admin_user)

    # Sin asignar: el recordatorio le llega de nuevo a todo el equipo de esa sucursal, no a otra.
    sin_asignar = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Limpiar la campana"}, headers=ha).json()
    avisos.clear()
    r = client.post(f"/api/ops/tasks/{sin_asignar['id']}/remind", headers=ha)
    assert r.status_code == 200, r.text
    avisados = {uid for a in avisos for uid in a.get("user_ids", [])}
    assert avisados == {clayton_agent.id, supervisor_user.id} and obarrio_agent.id not in avisados
    assert all("Recordatorio" in a["title"] for a in avisos)
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "task.remind", AuditEvent.entity_id == sin_asignar["id"]).count() == 1

    # Asignada: solo le llega a esa persona.
    asignada = client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Sacar la basura", "assigned_to_user_id": clayton_agent.id}, headers=ha).json()
    avisos.clear()
    assert client.post(f"/api/ops/tasks/{asignada['id']}/remind", headers=ha).status_code == 200
    assert [a["user_ids"] for a in avisos] == [[clayton_agent.id]]

    # Alguien de otra sucursal (ni siquiera encargado) no puede recordar esta tarea de Clayton.
    assert client.post(f"/api/ops/tasks/{asignada['id']}/remind", headers=_h(obarrio_agent)).status_code == 403

    # Una tarea cerrada no admite recordatorio.
    client.post(f"/api/ops/tasks/{asignada['id']}/status", json={"status": "hecha"}, headers=_h(clayton_agent, clayton_device))
    assert client.post(f"/api/ops/tasks/{asignada['id']}/remind", headers=ha).status_code == 409


# ---- equipo y resumen ------------------------------------------------------------

def test_equipo_de_la_sucursal_mas_globales(client, clayton_branch, clayton_agent, clayton_device, supervisor_user, admin_user, obarrio_agent):
    r = client.get(f"/api/ops/team?branch_id={clayton_branch.id}", headers=_h(admin_user))
    assert r.status_code == 200, r.text
    ids = {m["id"] for m in r.json()}
    assert clayton_agent.id in ids and supervisor_user.id in ids and admin_user.id in ids
    assert obarrio_agent.id not in ids
    # Un agente solo ve a los de su sucursal (el ?branch_id se ignora).
    r = client.get("/api/ops/team?branch_id=999", headers=_h(clayton_agent, clayton_device))
    assert r.status_code == 200 and obarrio_agent.id not in {m["id"] for m in r.json()}


def test_resumen_por_sucursal(client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device, supervisor_user, admin_user, avisos):
    hs = _h(supervisor_user, clayton_device)
    client.post("/api/ops/incidents", json={"branch_id": clayton_branch.id, "title": "Grave", "severity": "alta"}, headers=hs)
    client.post("/api/ops/incidents", json={"branch_id": clayton_branch.id, "title": "Leve", "severity": "baja"}, headers=hs)
    client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Vencida", "due_date": (datetime.utcnow() - timedelta(hours=1)).isoformat()}, headers=hs)
    client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "A tiempo"}, headers=hs)
    client.post("/api/ops/requests", json={"branch_id": clayton_branch.id, "item_name": "Aguacate"}, headers=hs)
    db_session.add(ExpectedShipment(branch_id=clayton_branch.id, expected_date=date.today() - timedelta(days=2), status="pendiente", created_by_user_id=supervisor_user.id))
    db_session.add(StockCount(branch_id=clayton_branch.id, counted_by_user_id=supervisor_user.id, counted_at=datetime.utcnow() - timedelta(days=10)))
    db_session.commit()

    r = client.get("/api/ops/overview", headers=_h(admin_user))
    assert r.status_code == 200, r.text
    data = r.json()
    por = {b["branch_code"]: b for b in data["branches"]}
    cly = por["CLY"]
    assert (cly["incidents_open"], cly["incidents_alta"], cly["tasks_pending"], cly["tasks_overdue"]) == (2, 1, 2, 1)
    assert cly["requests_open"] == 1 and cly["expected_overdue"] == 1 and cly["days_since_count"] == 10
    assert cly["attention"] > por["OBR"]["attention"]
    assert data["branches"][0]["branch_code"] == "CLY"   # la que más atención necesita, primero
    assert data["totals"]["incidents_open"] == 2 and data["totals"]["tasks_overdue"] == 1

    # Un supervisor local solo ve su sucursal.
    r = client.get("/api/ops/overview", headers=hs).json()
    assert [b["branch_code"] for b in r["branches"]] == ["CLY"]


# ---- traslados: avisos y sucursal ---------------------------------------------------

def test_los_traslados_avisan_a_la_sucursal_que_tiene_que_actuar(client, db_session, clayton_branch, obarrio_branch, admin_user, avisos):
    from models.inventory_item import InventoryItem
    item = InventoryItem(name="Tomate", unit="kg", category="Vegetales")
    db_session.add(item); db_session.commit(); db_session.refresh(item)
    h = _h(admin_user)
    t = client.post("/api/transfers/", json={"from_branch_id": clayton_branch.id, "to_branch_id": obarrio_branch.id, "items": [{"inventory_item_id": item.id, "quantity": "5"}]}, headers=h).json()
    assert avisos[-1]["branch_id"] == clayton_branch.id and "pedido por Obarrio" in avisos[-1]["title"]
    client.post(f"/api/transfers/{t['id']}/approve", headers=h)
    assert avisos[-1]["branch_id"] == obarrio_branch.id and "aprobado" in avisos[-1]["title"]
    client.post(f"/api/transfers/{t['id']}/dispatch", headers=h)
    assert avisos[-1]["branch_id"] == obarrio_branch.id and avisos[-1]["managers_only"] is False and "en camino" in avisos[-1]["title"]
    client.post(f"/api/transfers/{t['id']}/receive", headers=h)
    assert avisos[-1]["branch_id"] == clayton_branch.id and "recibido" in avisos[-1]["title"]


def test_crear_sucursal_guarda_el_horario(client, admin_user):
    r = client.post("/api/branches/", json={"name": "Santa María", "code": "SMA", "opens_at": "07:00", "closes_at": "22:00"}, headers=_h(admin_user))
    assert r.status_code == 201, r.text
    assert (r.json()["opens_at"], r.json()["closes_at"]) == ("07:00", "22:00")
