"""
Visor de actividad: admin ve todo; gerente (supervisor sin sucursal) ve todas las sucursales pero
no la administración del sistema; un encargado solo su sucursal; un agente no entra.
"""
from datetime import datetime, timedelta, timezone

import pytest

from models.audit import AuditEvent
from models.user import User
from security.auth import get_password_hash
from tests.conftest import auth_headers_for


@pytest.fixture
def eventos(db_session, admin_user, supervisor_user, clayton_branch, obarrio_branch):
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    filas = [
        AuditEvent(actor_user_id=supervisor_user.id, branch_id=clayton_branch.id, action="task.create", entity_type="task", entity_id=1, metadata_json='{"title": "Limpiar campana"}', created_at=ahora - timedelta(minutes=5)),
        AuditEvent(actor_user_id=supervisor_user.id, branch_id=clayton_branch.id, action="waste.create", entity_type="waste", entity_id=7, metadata_json=None, created_at=ahora - timedelta(minutes=4)),
        AuditEvent(actor_user_id=admin_user.id, branch_id=obarrio_branch.id, action="task.create", entity_type="task", entity_id=2, metadata_json="{}", created_at=ahora - timedelta(minutes=3)),
        AuditEvent(actor_user_id=admin_user.id, branch_id=clayton_branch.id, action="user.create", entity_type="user", entity_id=9, metadata_json='{"role": "agent"}', created_at=ahora - timedelta(minutes=2)),
        AuditEvent(actor_user_id=admin_user.id, branch_id=None, action="backup.create", entity_type="backup", entity_id=None, metadata_json='{"name": "x"}', created_at=ahora - timedelta(minutes=1)),
        AuditEvent(actor_user_id=admin_user.id, branch_id=clayton_branch.id, action="count.create", entity_type="stock_count", entity_id=3, metadata_json="no es json", created_at=ahora - timedelta(days=3)),
    ]
    db_session.add_all(filas); db_session.commit()
    return filas


def _acciones(r):
    return [e["action"] for e in r.json()["events"]]


def test_cada_rol_ve_lo_que_le_toca(client, db_session, eventos, admin_user, supervisor_user, clayton_agent, clayton_device, clayton_branch):
    # Admin: todo, lo más nuevo primero, con nombre de quien lo hizo y la sucursal.
    r = client.get("/api/system/audit", headers=auth_headers_for(admin_user))
    assert r.status_code == 200
    d = r.json()
    assert _acciones(r) == ["backup.create", "user.create", "task.create", "waste.create", "task.create", "count.create"]
    assert d["is_admin"] is True and d["events"][1]["actor"]["name"] == admin_user.name and d["events"][1]["branch"]["id"] == clayton_branch.id
    assert d["events"][-1]["metadata"] == {}   # metadata ilegible no rompe la lista

    # Gerente: todas las sucursales, sin usuarios ni respaldos ni eventos sin sucursal.
    gerente = User(username="gerente.l", name="Gerente", email="g@farmhouse.pa", password_hash=get_password_hash("x1234567"), role="supervisor", branch_id=None, active=True)
    db_session.add(gerente); db_session.commit()
    r = client.get("/api/system/audit", headers=auth_headers_for(gerente, clayton_device.device_id))
    assert _acciones(r) == ["task.create", "waste.create", "task.create", "count.create"] and r.json()["is_admin"] is False

    # Encargado de Clayton: solo Clayton, aunque pida otra sucursal.
    r = client.get("/api/system/audit?branch_id=999", headers=auth_headers_for(supervisor_user, clayton_device.device_id))
    assert {e["branch"]["id"] for e in r.json()["events"]} == {clayton_branch.id} and "user.create" not in _acciones(r)

    # Agente: sin permiso de reportes, no entra.
    assert client.get("/api/system/audit", headers=auth_headers_for(clayton_agent, clayton_device.device_id)).status_code == 403


def test_filtros_y_paginas(client, eventos, admin_user, supervisor_user):
    h = auth_headers_for(admin_user)
    assert _acciones(client.get("/api/system/audit?group=task", headers=h)) == ["task.create", "task.create"]
    assert set(_acciones(client.get(f"/api/system/audit?actor_user_id={supervisor_user.id}", headers=h))) == {"task.create", "waste.create"}
    hoy = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)).date()
    assert "count.create" not in _acciones(client.get(f"/api/system/audit?date_from={hoy}", headers=h))
    p1 = client.get("/api/system/audit?limit=4", headers=h).json()
    p2 = client.get("/api/system/audit?limit=4&offset=4", headers=h).json()
    assert p1["has_more"] is True and len(p1["events"]) == 4 and p2["has_more"] is False and len(p2["events"]) == 2
