"""
Vinculación real de dispositivos.

Antes bastaba con conocer el código público FH-DEVICE-… (que GET /devices/ listaba a cualquier
usuario con sesión) para pasar como equipo autorizado. Ahora autoriza un token secreto que solo
se obtiene canjeando, una vez, un código de vinculación que genera el administrador.
"""
from datetime import datetime, timedelta, timezone

from tests.conftest import auth_headers_for
from models.device import Device
from models.audit import AuditEvent
from services.device_access import hash_token, normalize_enroll_code


def _registrar(client, admin_user, clayton_branch, clayton_device, name="Tablet nueva"):
    res = client.post("/api/devices/", json={"name": name, "device_type": "tablet", "branch_id": clayton_branch.id},
                      headers=auth_headers_for(admin_user, clayton_device.device_id))
    assert res.status_code == 200, res.text
    return res.json()


def test_el_codigo_publico_ya_no_autoriza(client, db_session, clayton_branch, clayton_agent):
    """Un equipo registrado pero sin vincular: su código FH-DEVICE-… en el header no sirve."""
    dev = Device(device_id="FH-DEVICE-SINVINC", name="Tablet sin vincular", device_type="tablet",
                 branch_id=clayton_branch.id, status="active")
    db_session.add(dev)
    db_session.commit()

    res = client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, "FH-DEVICE-SINVINC"))
    assert res.status_code == 403
    assert "dispositivo" in res.json()["detail"].lower()


def test_listar_dispositivos_no_filtra_secretos(client, admin_user, clayton_device, clayton_branch):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    assert "enrollment_code" in creado  # solo en la respuesta de creación
    lista = client.get("/api/devices/", headers=auth_headers_for(admin_user, clayton_device.device_id)).json()
    nuevo = next(d for d in lista if d["id"] == creado["id"])
    for campo in ("enrollment_code", "secret_hash", "enroll_code_hash", "device_token"):
        assert campo not in nuevo
    assert nuevo["enrolled_at"] is None and nuevo["enroll_expires_at"] is not None


def test_vincular_con_codigo_y_operar(client, db_session, admin_user, clayton_device, clayton_branch, clayton_agent):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    code = creado["enrollment_code"]
    assert len(normalize_enroll_code(code)) == 8 and "-" in code

    # El agente lo escribe en minúsculas y sin guion: igual sirve.
    res = client.post("/api/devices/enroll", json={"code": code.lower().replace("-", "")},
                      headers=auth_headers_for(clayton_agent, None))
    assert res.status_code == 200, res.text
    token = res.json()["device_token"]
    assert len(token) >= 32 and res.json()["device"]["id"] == creado["id"]
    assert res.json()["device"]["enrolled_at"] is not None

    # Con el token sí opera…
    assert client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, token)).status_code == 200
    # …y /devices/me dice qué equipo es.
    me = client.get("/api/devices/me", headers=auth_headers_for(clayton_agent, token)).json()
    assert me["id"] == creado["id"]

    # En la base solo vive el hash.
    dev = db_session.query(Device).filter(Device.id == creado["id"]).first()
    assert dev.secret_hash == hash_token(token) and dev.enroll_code_hash is None
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "device.enrolled", AuditEvent.entity_id == dev.id).count() == 1

    # El código es de un solo uso.
    res2 = client.post("/api/devices/enroll", json={"code": code}, headers=auth_headers_for(clayton_agent, None))
    assert res2.status_code == 400


def test_codigo_vencido_o_inventado(client, db_session, admin_user, clayton_device, clayton_branch, clayton_agent):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    dev = db_session.query(Device).filter(Device.id == creado["id"]).first()
    dev.enroll_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db_session.commit()

    res = client.post("/api/devices/enroll", json={"code": creado["enrollment_code"]}, headers=auth_headers_for(clayton_agent, None))
    assert res.status_code == 400 and "venci" in res.json()["detail"].lower()
    assert client.post("/api/devices/enroll", json={"code": "ZZZZ-9999"}, headers=auth_headers_for(clayton_agent, None)).status_code == 400
    assert client.post("/api/devices/enroll", json={"code": "ABC"}, headers=auth_headers_for(clayton_agent, None)).status_code == 422


def test_agente_no_vincula_equipos_de_otra_sucursal(client, admin_user, clayton_device, obarrio_branch, clayton_agent, supervisor_user):
    res = client.post("/api/devices/", json={"name": "PC Obarrio 2", "device_type": "computadora", "branch_id": obarrio_branch.id},
                      headers=auth_headers_for(admin_user, clayton_device.device_id))
    code = res.json()["enrollment_code"]
    # Agente de Clayton: no.
    assert client.post("/api/devices/enroll", json={"code": code}, headers=auth_headers_for(clayton_agent, None)).status_code == 403
    # Supervisor de Clayton: tampoco.
    assert client.post("/api/devices/enroll", json={"code": code}, headers=auth_headers_for(supervisor_user, None)).status_code == 403
    # El admin sí (es quien instala las tablets).
    assert client.post("/api/devices/enroll", json={"code": code}, headers=auth_headers_for(admin_user, None)).status_code == 200


def test_nuevo_codigo_reemplaza_al_anterior_y_al_token_viejo(client, admin_user, clayton_device, clayton_branch, clayton_agent):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    viejo = creado["enrollment_code"]
    res = client.post(f"/api/devices/{creado['id']}/enrollment-code", headers=auth_headers_for(admin_user, clayton_device.device_id))
    assert res.status_code == 200
    nuevo = res.json()["enrollment_code"]
    assert nuevo != viejo
    assert client.post("/api/devices/enroll", json={"code": viejo}, headers=auth_headers_for(clayton_agent, None)).status_code == 400
    token1 = client.post("/api/devices/enroll", json={"code": nuevo}, headers=auth_headers_for(clayton_agent, None)).json()["device_token"]

    # Se vuelve a vincular (tablet reemplazada): el navegador viejo queda fuera.
    otro = client.post(f"/api/devices/{creado['id']}/enrollment-code", headers=auth_headers_for(admin_user, clayton_device.device_id)).json()["enrollment_code"]
    token2 = client.post("/api/devices/enroll", json={"code": otro}, headers=auth_headers_for(clayton_agent, None)).json()["device_token"]
    assert client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, token1)).status_code == 403
    assert client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, token2)).status_code == 200


def test_revocar_mata_el_token_en_el_acto(client, db_session, admin_user, clayton_device, clayton_branch, clayton_agent):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    token = client.post("/api/devices/enroll", json={"code": creado["enrollment_code"]},
                        headers=auth_headers_for(clayton_agent, None)).json()["device_token"]
    assert client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, token)).status_code == 200

    assert client.post(f"/api/devices/{creado['id']}/revoke", headers=auth_headers_for(admin_user, clayton_device.device_id)).status_code == 200
    assert client.get("/api/conversations/", headers=auth_headers_for(clayton_agent, token)).status_code == 403
    dev = db_session.query(Device).filter(Device.id == creado["id"]).first()
    assert dev.secret_hash is None and dev.enrolled_at is None

    # Revocado: no se le puede generar código hasta reactivarlo.
    assert client.post(f"/api/devices/{creado['id']}/enrollment-code", headers=auth_headers_for(admin_user, clayton_device.device_id)).status_code == 400
    client.put(f"/api/devices/{creado['id']}", json={"status": "active"}, headers=auth_headers_for(admin_user, clayton_device.device_id))
    assert client.post(f"/api/devices/{creado['id']}/enrollment-code", headers=auth_headers_for(admin_user, clayton_device.device_id)).status_code == 200


def test_solo_admin_registra_o_genera_codigos(client, admin_user, clayton_device, clayton_branch, clayton_agent):
    creado = _registrar(client, admin_user, clayton_branch, clayton_device)
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert client.post("/api/devices/", json={"name": "X", "device_type": "tablet", "branch_id": clayton_branch.id}, headers=h).status_code == 403
    assert client.post(f"/api/devices/{creado['id']}/enrollment-code", headers=h).status_code == 403
