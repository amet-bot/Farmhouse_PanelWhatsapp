"""
Contratos de colaboradores (/contratos) y el formulario que llena cada colaborador (/colaborador).

Son cédulas, cuentas bancarias, salarios y datos de salud de todo el personal, así que lo que más se
prueba es la seguridad: solo el administrador entra; los datos van cifrados en la base; las listas no
muestran datos sensibles; cada lectura de detalle queda en auditoría (sin datos sensibles); el
formulario público exige una invitación vigente de un solo uso y no revela cuáles enlaces existen;
y sin clave de cifrado en producción todo falla cerrado.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text

from services import intake_client
from services.intake_client import IntakeServiceError
from tests.conftest import auth_headers_for

INFO = b"farmhouse-intake-v1"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def encrypt_envelope(public_key, obj) -> dict:
    """Lo mismo que hace public_intake/static/crypto.js en el navegador del colaborador."""
    eph = ec.generate_private_key(ec.SECP256R1())
    shared = eph.exchange(ec.ECDH(), public_key)
    salt, iv = os.urandom(16), os.urandom(12)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(shared)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj).encode(), INFO)
    epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"v": 1, "epk": _b64u(epk), "salt": _b64u(salt), "iv": _b64u(iv), "ct": _b64u(ct)}


class FakeIntakeService:
    """El servicio público (public_intake/) en memoria: guarda hashes de invitaciones y sobres cifrados."""

    def __init__(self, public_key):
        self.public_key = public_key
        self.invites = {}
        self.submissions = []
        self._next = 1
        self.down = False
        self.fail_ack = False
        self.acked = []
        self.revoked = []
        self.open = False

    def _check(self):
        if self.down:
            raise IntakeServiceError("No se pudo contactar el formulario público.")

    def register_invite(self, token_hash, label, expires_at):
        self._check()
        self.invites[token_hash] = label

    def revoke_invite(self, token_hash):
        self._check()
        self.invites.pop(token_hash, None)
        self.revoked.append(token_hash)

    def fetch_submissions(self, limit=50):
        self._check()
        return [dict(s) for s in self.submissions][:limit]

    def ack_submission(self, remote_id):
        self._check()
        if self.fail_ack:
            raise IntakeServiceError("ack falló")
        self.acked.append(remote_id)
        self.submissions = [s for s in self.submissions if s["id"] != remote_id]

    def get_open_form(self):
        self._check()
        return {"enabled": self.open, "today": 0, "daily_cap": 150, "pending": len(self.submissions)}

    def set_open_form(self, enabled):
        self._check()
        self.open = bool(enabled)
        return self.get_open_form()

    def submit(self, person, invite_hash=None, envelope=None):
        """Lo que haría un colaborador: cifra sus datos con la llave pública y los deja en el buzón."""
        sub = {"id": self._next, "invite_hash": invite_hash or "0" * 64,
               "envelope": envelope or encrypt_envelope(self.public_key, person), "created_at": "2026-10-05T12:00:00"}
        self._next += 1
        self.submissions.append(sub)
        return sub["id"]


@pytest.fixture
def intake_service(monkeypatch):
    from config import settings
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setattr(settings, "INTAKE_PRIVATE_KEY", base64.b64encode(pem).decode())
    monkeypatch.setattr(settings, "INTAKE_SERVICE_URL", "https://intake.test")
    monkeypatch.setattr(settings, "INTAKE_ADMIN_KEY", "k" * 40)
    fake = FakeIntakeService(private.public_key())
    for name in ("register_invite", "revoke_invite", "fetch_submissions", "ack_submission", "get_open_form", "set_open_form"):
        monkeypatch.setattr(intake_client, name, getattr(fake, name))
    return fake


def _person(**over):
    data = {
        "first_name": "Vladimir Humberto", "last_name": "Lay Zaldivar", "birth_date": "1990-05-10",
        "gender": "M", "nationality": "Panameño", "marital_status": "Casado", "blood_type": "O+",
        "id_type": "Cedula", "id_number": "8-753-442", "dv": "", "phone": "69998888",
        "email": "Vlad@Example.com", "address": "Ciudad de Panamá",
        "emergency_contact_name": "María Pérez", "emergency_contact_phone": "62223333",
        "emergency_contact_relationship": "Madre",
        "bank_name": "Banco General", "account_type": "Ahorros", "account_number": "416973192777",
        "dependents": [{"name": "Megan Lucia Lay", "age": 8, "relationship": "Hija"}],
    }
    data.update(over)
    return data


def _payload(**over):
    data = _person()
    data.update({
        "position": "Asistente de Cocina", "contract_type": "Definido", "salary": "700.00",
        "start_date": "2026-06-19", "end_date": "2026-12-19", "notes": "",
        "duties": "Preparación del espacio: mantener el espacio ordenado,\nControl de calidad: revisar los ingredientes,",
        "document_url": "",
    })
    data.update(over)
    return data


def _post(client, headers, **over):
    return client.post("/api/contracts", json=_payload(**over), headers=headers)


def _invite(client, headers, **body):
    res = client.post("/api/contracts/invites", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _token_of(invite) -> str:
    return invite["link"].split("#t=")[1]


def _hash_of(invite) -> str:
    return hashlib.sha256(_token_of(invite).encode()).hexdigest()


# --------------------------------------------------------------------------
# Contratos (administración)
# --------------------------------------------------------------------------
def test_admin_creates_contract_and_data_is_normalized(client, admin_user):
    headers = auth_headers_for(admin_user)
    res = _post(client, headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] == "vlad@example.com"
    assert body["phone"] == "6999-8888"
    assert body["emergency_contact_phone"] == "6222-3333"
    assert body["blood_type"] == "O+"
    assert body["emergency_contact_name"] == "María Pérez"
    assert body["emergency_contact_relationship"] == "Madre"
    assert body["dv"] is None and body["notes"] is None and body["document_url"] is None
    assert body["dependents"] == [{"name": "Megan Lucia Lay", "age": 8, "relationship": "Hija"}]
    assert float(body["salary"]) == 700.0
    assert client.get(f"/api/contracts/{body['id']}", headers=headers).json()["id_number"] == "8-753-442"


def test_list_is_a_summary_without_sensitive_data(client, admin_user):
    headers = auth_headers_for(admin_user)
    _post(client, headers)
    listed = client.get("/api/contracts", headers=headers)
    assert listed.status_code == 200
    row = listed.json()[0]
    assert set(row) == {"id", "first_name", "last_name", "id_type", "id_number_masked", "position",
                        "contract_type", "staff_area", "start_date", "end_date", "document_url"}
    assert "753" not in row["id_number_masked"] or row["id_number_masked"].startswith("8-")
    assert row["id_number_masked"] != "8-753-442"
    blob = listed.text
    for secret in ("416973192777", "vlad@example.com", "6999-8888", "O+", "Ciudad de Panamá", "700", "María Pérez"):
        assert secret not in blob, secret


def test_sensitive_fields_are_encrypted_in_the_database(client, db_session, admin_user):
    headers = auth_headers_for(admin_user)
    _post(client, headers)
    row = db_session.execute(text("SELECT * FROM employee_contracts")).mappings().one()
    for column in ("birth_date", "blood_type", "id_number", "dv", "phone", "email", "address",
                   "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship",
                   "bank_name", "account_number", "salary", "dependents_json"):
        value = row[column]
        if column == "dv":
            assert value is None
            continue
        assert str(value).startswith("enc:v1:"), column
    raw = json.dumps({k: str(v) for k, v in row.items()}, ensure_ascii=False)
    for secret in ("8-753-442", "416973192777", "vlad@example.com", "6999-8888", "1990-05-10",
                   "Ciudad de Panamá", "María Pérez", "Megan", "700.00", "Banco General"):
        assert secret not in raw, secret
    assert re.fullmatch(r"[0-9a-f]{64}", row["id_number_hash"]) and "8-753-442" not in row["id_number_hash"]


def test_blood_type_and_emergency_contact_are_required(client, admin_user):
    headers = auth_headers_for(admin_user)
    assert _post(client, headers, blood_type="Z+").status_code == 422
    for field in ("blood_type", "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship"):
        data = _payload()
        del data[field]
        assert client.post("/api/contracts", json=data, headers=headers).status_code == 422, field
    assert _post(client, headers, emergency_contact_phone="123").status_code == 422


def test_validation_rejects_bad_data(client, admin_user):
    headers = auth_headers_for(admin_user)
    bad = [
        {"id_number": "abc"}, {"id_type": "Pasaporte", "id_number": "1"}, {"email": "no-es-correo"},
        {"phone": "12"}, {"account_number": "12ab"}, {"salary": "0"}, {"gender": "X"},
        {"contract_type": "Inventado"}, {"birth_date": "2999-01-01"}, {"end_date": "2026-06-01"},
        {"end_date": None}, {"duties": ""}, {"document_url": "ftp://x"}, {"dv": "123"},
    ]
    for over in bad:
        assert _post(client, headers, **over).status_code == 422, over


def test_indefinite_contract_needs_no_end_date_or_duties(client, admin_user):
    headers = auth_headers_for(admin_user)
    res = _post(client, headers, contract_type="Indefinido", end_date=None, duties="")
    assert res.status_code == 201, res.text
    assert res.json()["end_date"] is None


def test_staff_area_defaults_to_branch_and_admin_is_kept(client, admin_user):
    # El área decide la plantilla del Word: sucursal (Definido) o administrativo (Indefinido).
    headers = auth_headers_for(admin_user)
    branch = _post(client, headers)
    assert branch.status_code == 201, branch.text
    assert branch.json()["staff_area"] == "Sucursal"

    office = _post(client, headers, first_name="Laura", id_number="8-111-2222", contract_type="Indefinido",
                   end_date=None, staff_area="Administrativo", position="Coordinadora administrativa")
    assert office.status_code == 201, office.text
    cid = office.json()["id"]
    assert client.get(f"/api/contracts/{cid}", headers=headers).json()["staff_area"] == "Administrativo"
    listed = {r["id"]: r for r in client.get("/api/contracts", headers=headers).json()}
    assert listed[cid]["staff_area"] == "Administrativo"

    # Editar conserva el área elegida.
    upd = client.put(f"/api/contracts/{cid}", json=_payload(first_name="Laura", id_number="8-111-2222", contract_type="Indefinido",
                                                            end_date=None, staff_area="Administrativo"), headers=headers)
    assert upd.status_code == 200 and upd.json()["staff_area"] == "Administrativo"

    bad = _post(client, headers, first_name="Otro", id_number="8-333-4444", staff_area="Bodega")
    assert bad.status_code == 422


def test_renewal_allowed_but_same_contract_is_duplicate(client, admin_user):
    headers = auth_headers_for(admin_user)
    assert _post(client, headers).status_code == 201
    assert _post(client, headers, start_date="2026-12-20", end_date="2027-06-20").status_code == 201
    assert _post(client, headers).status_code == 409
    # Mismo contrato escrito con otro formato (minúsculas): el índice ciego normaliza.
    assert _post(client, headers, id_type="Pasaporte", id_number="av515144").status_code == 201
    assert _post(client, headers, id_type="Pasaporte", id_number="AV515144").status_code == 409


def test_update_and_delete(client, admin_user):
    headers = auth_headers_for(admin_user)
    cid = _post(client, headers).json()["id"]
    res = client.put(f"/api/contracts/{cid}", json=_payload(position="Supervisor", salary="900"), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["position"] == "Supervisor"
    assert float(client.get(f"/api/contracts/{cid}", headers=headers).json()["salary"]) == 900.0
    assert client.delete(f"/api/contracts/{cid}", headers=headers).status_code == 204
    assert client.get(f"/api/contracts/{cid}", headers=headers).status_code == 404
    assert client.put("/api/contracts/9999", json=_payload(), headers=headers).status_code == 404


def test_search_by_name_position_or_exact_id_number(client, admin_user):
    headers = auth_headers_for(admin_user)
    _post(client, headers)
    _post(client, headers, first_name="Ana", last_name="Pérez", id_number="8-100-200", position="Cajera")
    assert [c["first_name"] for c in client.get("/api/contracts?q=Ana", headers=headers).json()] == ["Ana"]
    assert [c["first_name"] for c in client.get("/api/contracts?q=Cajera", headers=headers).json()] == ["Ana"]
    assert [c["last_name"] for c in client.get("/api/contracts?q=8-753-442", headers=headers).json()] == ["Lay Zaldivar"]
    assert client.get("/api/contracts?q=8-753", headers=headers).json() == []      # la cédula se busca exacta


def test_export_returns_full_data_for_admin_and_is_audited(client, db_session, admin_user):
    from models.audit import AuditEvent
    headers = auth_headers_for(admin_user)
    _post(client, headers)
    rows = client.get("/api/contracts/export", headers=headers).json()
    assert rows[0]["account_number"] == "416973192777" and rows[0]["blood_type"] == "O+"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "contract.export").count() == 1


def test_only_admin_can_use_contracts_and_invites(client, admin_user, clayton_agent, supervisor_user, clayton_device, intake_service):
    admin_headers = auth_headers_for(admin_user)
    cid = _post(client, admin_headers).json()["id"]
    iid = _invite(client, admin_headers)["id"]
    for user in (clayton_agent, supervisor_user):
        h = auth_headers_for(user, clayton_device.device_id)
        calls = [
            client.get("/api/contracts", headers=h), client.get("/api/contracts/export", headers=h),
            client.get(f"/api/contracts/{cid}", headers=h), client.post("/api/contracts", json=_payload(), headers=h),
            client.put(f"/api/contracts/{cid}", json=_payload(), headers=h), client.delete(f"/api/contracts/{cid}", headers=h),
            client.post("/api/contracts/invites", json={}, headers=h), client.get("/api/contracts/invites", headers=h),
            client.delete(f"/api/contracts/invites/{iid}", headers=h), client.get("/api/contracts/intakes", headers=h),
            client.get("/api/contracts/intakes/1", headers=h), client.delete("/api/contracts/intakes/1", headers=h),
            client.post("/api/contracts/intakes/sync", headers=h),
        ]
        assert [c.status_code for c in calls] == [403] * len(calls)
    assert client.get("/api/contracts").status_code == 401
    assert client.get("/api/contracts/intakes").status_code == 401


def test_audit_trail_records_reads_without_sensitive_data(client, db_session, admin_user):
    from models.audit import AuditEvent
    headers = auth_headers_for(admin_user)
    cid = _post(client, headers).json()["id"]
    client.get("/api/contracts", headers=headers)
    client.get(f"/api/contracts/{cid}", headers=headers)
    client.delete(f"/api/contracts/{cid}", headers=headers)
    events = db_session.query(AuditEvent).filter(AuditEvent.entity_type == "employee_contract").all()
    assert {e.action for e in events} == {"contract.create", "contract.list", "contract.read", "contract.delete"}
    blob = " ".join((e.metadata_json or "") for e in events)
    for secret in ("416973192777", "8-753-442", "vlad@example.com", "O+", "700"):
        assert secret not in blob


def test_responses_are_never_cached(client, admin_user):
    headers = auth_headers_for(admin_user)
    cid = _post(client, headers).json()["id"]
    for url in ("/api/contracts", f"/api/contracts/{cid}", "/api/contracts/export", "/api/contracts/intakes"):
        assert "no-store" in client.get(url, headers=headers).headers.get("cache-control", ""), url


def test_admin_permissions_include_contracts_and_others_lack_it():
    from security.permissions import resolve_permissions
    assert "contracts.manage" in resolve_permissions("admin")
    assert "contracts.manage" not in resolve_permissions("agent")
    assert "contracts.manage" not in resolve_permissions("supervisor")


# --------------------------------------------------------------------------
# Invitaciones y solicitudes (el formulario público es un servicio aparte: public_intake/)
# --------------------------------------------------------------------------
def test_invite_sends_only_the_hash_to_the_public_service_and_returns_the_link(client, db_session, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    inv = _invite(client, headers, label="Ana Pérez", hours=24)
    assert inv["link"].startswith("https://intake.test/colaborador#t=")
    token = _token_of(inv)
    assert len(token) >= 40
    assert list(intake_service.invites) == [_hash_of(inv)]              # al servicio solo viaja el hash
    assert token not in json.dumps(intake_service.invites)
    stored = db_session.execute(text("SELECT token_hash FROM employee_invites")).scalar_one()
    assert stored == _hash_of(inv) and token not in stored
    listed = client.get("/api/contracts/invites", headers=headers)
    assert listed.json()[0]["label"] == "Ana Pérez" and token not in listed.text and listed.json()[0]["link"] is None


def test_invite_needs_the_public_service(client, admin_user, db_session, intake_service):
    headers = auth_headers_for(admin_user)
    intake_service.down = True
    res = client.post("/api/contracts/invites", json={}, headers=headers)
    assert res.status_code == 502
    assert db_session.execute(text("SELECT COUNT(*) FROM employee_invites")).scalar_one() == 0   # sin servicio no queda una invitación a medias


def test_invite_is_unavailable_when_the_service_is_not_configured(client, admin_user, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "INTAKE_SERVICE_URL", None)
    assert client.post("/api/contracts/invites", json={}, headers=auth_headers_for(admin_user)).status_code == 503


def test_invite_hours_are_bounded(client, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    assert client.post("/api/contracts/invites", json={"hours": 0}, headers=headers).status_code == 422
    assert client.post("/api/contracts/invites", json={"hours": 5000}, headers=headers).status_code == 422


def test_revoking_an_invite_also_revokes_it_at_the_public_service(client, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    inv = _invite(client, headers)
    intake_service.down = True
    assert client.delete(f"/api/contracts/invites/{inv['id']}", headers=headers).status_code == 502       # el enlace seguiría vivo: no se borra acá
    assert len(client.get("/api/contracts/invites", headers=headers).json()) == 1
    intake_service.down = False
    assert client.delete(f"/api/contracts/invites/{inv['id']}", headers=headers).status_code == 204
    assert intake_service.revoked == [_hash_of(inv)]
    assert client.delete(f"/api/contracts/invites/{inv['id']}", headers=headers).status_code == 404


def test_full_flow_collaborator_fills_form_and_admin_converts_it(client, db_session, admin_user, intake_service):
    from models.audit import AuditEvent
    headers = auth_headers_for(admin_user)
    inv = _invite(client, headers, label="Vladimir")
    intake_service.submit(_person(), invite_hash=_hash_of(inv))         # el colaborador llena el formulario (cifrado en su navegador)

    synced = client.post("/api/contracts/intakes/sync", headers=headers).json()
    assert synced == {"configured": True, "ok": True, "imported": 1, "rejected": 0}
    assert intake_service.submissions == [] and len(intake_service.acked) == 1      # se borra del servicio público

    summaries = client.get("/api/contracts/intakes", headers=headers).json()
    assert set(summaries[0]) == {"id", "first_name", "last_name", "id_type", "id_number_masked", "created_at", "open_form"}
    assert summaries[0]["open_form"] is False                         # llegó con invitación personal
    detail = client.get(f"/api/contracts/intakes/{summaries[0]['id']}", headers=headers).json()
    assert detail["blood_type"] == "O+" and detail["emergency_contact_name"] == "María Pérez"
    assert detail["account_number"] == "416973192777" and detail["dependents"][0]["name"] == "Megan Lucia Lay"
    assert client.get("/api/contracts/invites", headers=headers).json() == []        # la invitación quedó usada

    created = _post(client, headers, intake_id=summaries[0]["id"])
    assert created.status_code == 201, created.text
    assert client.get("/api/contracts/intakes", headers=headers).json() == []        # no queda una segunda copia
    assert db_session.execute(text("SELECT COUNT(*) FROM employee_intakes")).scalar_one() == 0
    actions = {e.action for e in db_session.query(AuditEvent).filter(AuditEvent.entity_type == "employee_intake")}
    assert {"intake.submit", "intake.list", "intake.read", "intake.convert"} <= actions


def test_imported_data_is_encrypted_in_the_database(client, db_session, admin_user, intake_service):
    intake_service.submit(_person())
    client.post("/api/contracts/intakes/sync", headers=auth_headers_for(admin_user))
    row = db_session.execute(text("SELECT * FROM employee_intakes")).mappings().one()
    raw = json.dumps({k: str(v) for k, v in row.items()}, ensure_ascii=False)
    for secret in ("8-753-442", "416973192777", "vlad@example.com", "1990-05-10", "María Pérez", "O+", "Megan"):
        assert secret not in raw, secret


def test_importing_twice_never_duplicates_even_if_the_receipt_fails(client, db_session, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    intake_service.submit(_person())
    intake_service.fail_ack = True
    assert client.post("/api/contracts/intakes/sync", headers=headers).json()["imported"] == 1
    assert len(intake_service.submissions) == 1                          # no se pudo avisar de recibido: sigue allá
    assert client.post("/api/contracts/intakes/sync", headers=headers).json()["imported"] == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM employee_intakes")).scalar_one() == 1
    intake_service.fail_ack = False
    client.post("/api/contracts/intakes/sync", headers=headers)
    assert intake_service.submissions == []                              # ya se pudo avisar y se limpió


def test_tampered_or_hand_made_envelopes_are_rejected_not_imported(client, db_session, admin_user, intake_service):
    from models.audit import AuditEvent
    headers = auth_headers_for(admin_user)
    good = encrypt_envelope(intake_service.public_key, _person())
    tampered = dict(good, ct=good["ct"][:-6] + "AAAAAA")
    intake_service.submit(None, envelope=tampered)                                                    # alterado en el camino
    intake_service.submit(_person(id_number="no-es-cedula"))                                          # cifrado bien, pero datos que el formulario real nunca manda
    intake_service.submit(None, envelope=encrypt_envelope(ec.generate_private_key(ec.SECP256R1()).public_key(), _person()))   # cifrado con otra llave
    synced = client.post("/api/contracts/intakes/sync", headers=headers).json()
    assert synced["imported"] == 0 and synced["rejected"] == 3
    assert intake_service.submissions == []
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "intake.rejected").count() == 3


def test_a_bad_private_key_never_discards_submissions(client, admin_user, intake_service, monkeypatch):
    from config import settings
    headers = auth_headers_for(admin_user)
    intake_service.submit(_person())
    monkeypatch.setattr(settings, "INTAKE_PRIVATE_KEY", base64.b64encode(b"esto no es una llave").decode())
    synced = client.post("/api/contracts/intakes/sync", headers=headers).json()
    assert synced["ok"] is False and synced["imported"] == 0
    assert len(intake_service.submissions) == 1 and intake_service.acked == []     # nada se borra por un error de configuración


def test_public_service_down_does_not_break_the_admin_screen(client, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    intake_service.down = True
    assert client.post("/api/contracts/intakes/sync", headers=headers).json()["ok"] is False
    assert client.get("/api/contracts/intakes", headers=headers).status_code == 200
    assert client.get("/api/contracts", headers=headers).status_code == 200


def test_sync_without_configuration_is_a_harmless_noop(client, admin_user, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "INTAKE_SERVICE_URL", None)
    assert client.post("/api/contracts/intakes/sync", headers=auth_headers_for(admin_user)).json() == {
        "configured": False, "ok": True, "imported": 0, "rejected": 0}


def test_open_form_switch_and_link(client, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    state = client.get("/api/contracts/open-form", headers=headers).json()
    assert state["enabled"] is False and state["link"] == "https://intake.test/colaborador"
    assert "#t=" not in state["link"]                                  # el enlace abierto no lleva token
    assert client.put("/api/contracts/open-form", json={"enabled": True}, headers=headers).json()["enabled"] is True
    assert intake_service.open is True
    assert client.put("/api/contracts/open-form", json={"enabled": False}, headers=headers).json()["enabled"] is False
    intake_service.down = True
    assert client.put("/api/contracts/open-form", json={"enabled": True}, headers=headers).status_code == 502


def test_open_form_switch_is_audited_and_admin_only(client, db_session, admin_user, clayton_agent, clayton_device, intake_service):
    from models.audit import AuditEvent
    client.put("/api/contracts/open-form", json={"enabled": True}, headers=auth_headers_for(admin_user))
    client.put("/api/contracts/open-form", json={"enabled": False}, headers=auth_headers_for(admin_user))
    assert {e.action for e in db_session.query(AuditEvent).filter(AuditEvent.action.like("openform.%"))} == {"openform.enable", "openform.disable"}
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert client.get("/api/contracts/open-form", headers=h).status_code == 403
    assert client.put("/api/contracts/open-form", json={"enabled": True}, headers=h).status_code == 403


def test_submissions_from_the_open_form_are_flagged_and_imported(client, admin_user, intake_service):
    headers = auth_headers_for(admin_user)
    intake_service.submit(_person(), invite_hash="open")
    assert client.post("/api/contracts/intakes/sync", headers=headers).json()["imported"] == 1
    assert client.get("/api/contracts/intakes", headers=headers).json()[0]["open_form"] is True


def test_central_system_exposes_nothing_for_the_public_form(client, admin_user, intake_service):
    """El sistema central no recibe nada de internet para el formulario: ni endpoints ni la página."""
    inv = _invite(client, auth_headers_for(admin_user))
    for path in ("/api/contracts/public/submit", "/api/contracts/public/check", "/api/contracts/public-key"):
        assert client.post(path, json={"token": _token_of(inv)}, headers={"X-Requested-With": "XMLHttpRequest"}).status_code in (404, 405)
    assert client.get("/colaborador").status_code == 404


def test_https_is_required_for_the_public_service_in_production(monkeypatch):
    from config import settings
    monkeypatch.undo()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "INTAKE_SERVICE_URL", "http://intake.example")
    monkeypatch.setattr(settings, "INTAKE_ADMIN_KEY", "k" * 40)
    with pytest.raises(IntakeServiceError):
        intake_client.fetch_submissions()


def test_intake_can_be_dismissed_and_old_ones_are_purged(client, db_session, admin_user, intake_service):
    from routers import contracts as contracts_router
    from models.contract import EmployeeIntake
    from models.audit import AuditEvent
    headers = auth_headers_for(admin_user)
    intake_service.submit(_person())
    intake_service.submit(_person(id_number="8-100-200"))
    client.post("/api/contracts/intakes/sync", headers=headers)
    first, second = client.get("/api/contracts/intakes", headers=headers).json()
    assert client.delete(f"/api/contracts/intakes/{first['id']}", headers=headers).status_code == 204
    assert db_session.query(EmployeeIntake).count() == 1
    old = db_session.query(EmployeeIntake).one()
    old.created_at = old.created_at - timedelta(days=contracts_router.INTAKE_RETENTION_DAYS + 1)
    db_session.commit()
    assert client.get("/api/contracts/intakes", headers=headers).json() == []
    assert db_session.query(EmployeeIntake).count() == 0
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "intake.purge").count() == 1


def test_converting_a_missing_intake_is_a_clear_404(client, admin_user):
    assert _post(client, auth_headers_for(admin_user), intake_id=999).status_code == 404


def test_browser_encryption_script_is_compatible_with_the_server(admin_user, intake_service, tmp_path):
    """Interoperabilidad real: el crypto.js que corre en el navegador del colaborador cifra y este sistema abre el sobre."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node no está instalado")
    from services.intake_crypto import decrypt_envelope
    crypto_js = Path(__file__).resolve().parents[2] / "public_intake" / "static" / "crypto.js"
    pub = intake_service.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    script = tmp_path / "enc.js"
    script.write_text(
        "require(%r);\nconst IC = globalThis.IntakeCrypto;\n"
        "IC.encrypt(%r, {hola: 'mundo', n: 7, tilde: 'Panamá ñ'}).then(e => console.log(JSON.stringify(e)));"
        % (str(crypto_js), _b64u(pub)), encoding="utf-8")
    out = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert decrypt_envelope(json.loads(out.stdout)) == {"hola": "mundo", "n": 7, "tilde": "Panamá ñ"}


# --------------------------------------------------------------------------
# Cifrado: claves
# --------------------------------------------------------------------------
def test_without_an_encryption_key_production_fails_closed(client, monkeypatch, admin_user):
    from config import settings
    headers = auth_headers_for(admin_user)
    cid = _post(client, headers).json()["id"]
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", None)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY_PREVIOUS", None)
    res = client.get(f"/api/contracts/{cid}", headers=headers)
    assert res.status_code == 503
    assert "cifrado" in res.json()["detail"]
    assert client.post("/api/contracts", json=_payload(start_date="2027-01-01", end_date="2027-07-01"), headers=headers).status_code == 503


def test_key_rotation_keeps_old_data_readable_and_searchable(client, monkeypatch, admin_user):
    from cryptography.fernet import Fernet
    from config import settings
    headers = auth_headers_for(admin_user)
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", old_key)
    cid = _post(client, headers).json()["id"]
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", new_key)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY_PREVIOUS", old_key)
    assert client.get(f"/api/contracts/{cid}", headers=headers).json()["id_number"] == "8-753-442"
    assert len(client.get("/api/contracts?q=8-753-442", headers=headers).json()) == 1
    # Sin la clave anterior ya no se puede leer: el dato no se devuelve a medias.
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY_PREVIOUS", None)
    with pytest.raises(ValueError):
        client.get(f"/api/contracts/{cid}", headers=headers)


def test_field_crypto_roundtrip_and_blind_index():
    from datetime import date
    from decimal import Decimal
    from services.field_crypto import EncryptedDate, EncryptedDecimal, EncryptedText, blind_index, mask_id
    t, d, n = EncryptedText(), EncryptedDate(), EncryptedDecimal()
    assert t.process_result_value(t.process_bind_param("hola", None), None) == "hola"
    assert d.process_result_value(d.process_bind_param(date(1990, 5, 10), None), None) == date(1990, 5, 10)
    assert n.process_result_value(n.process_bind_param(Decimal("700.50"), None), None) == Decimal("700.50")
    assert t.process_bind_param("hola", None) != t.process_bind_param("hola", None)     # IV distinto cada vez
    assert blind_index("8-753-442") == blind_index(" 8-753-442 ") == blind_index("8-753-442".lower())
    assert blind_index("8-753-442") != blind_index("8-753-443")
    assert blind_index(None) is None and blind_index("  ") is None
    assert mask_id("8-753-442") == "8-••••442" and mask_id("") == ""
    with pytest.raises(ValueError):
        t.process_result_value("texto-en-claro", None)                                   # nada sin cifrar pasa como válido
    tampered = t.process_bind_param("hola", None)[:-4] + "AAAA"
    with pytest.raises(ValueError):
        t.process_result_value(tampered, None)


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------
def test_admin_page_is_served(client):
    res = client.get("/contratos")
    assert res.status_code == 200 and "Contratos" in res.text


# --------------------------------------------------------------------------
# Rol RR.HH.: solo Contratos
# --------------------------------------------------------------------------
@pytest.fixture
def rrhh_user(db_session):
    from models.user import User
    from security.auth import get_password_hash
    user = User(username="venus", name="Venus RRHH", email="venus@example.com",
                password_hash=get_password_hash("una-clave-larga-de-prueba"), role="rrhh", branch_id=None, active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_rrhh_role_has_only_the_contracts_permission():
    from security.permissions import resolve_permissions
    assert resolve_permissions("rrhh") == {"contracts.manage"}


def test_rrhh_can_manage_contracts(client, rrhh_user, intake_service):
    headers = auth_headers_for(rrhh_user)
    created = _post(client, headers)
    assert created.status_code == 201, created.text
    assert client.get("/api/contracts", headers=headers).status_code == 200
    assert client.post("/api/contracts/invites", json={}, headers=headers).status_code == 201
    assert "contracts.manage" in client.get("/api/auth/me", headers=headers).json()["permissions"]


def test_rrhh_cannot_reach_anything_else(client, db_session, rrhh_user, clayton_branch, clayton_device):
    """Barrido de denegación por defecto: con un usuario RR.HH. (con o sin dispositivo registrado) ningún
    GET del sistema, fuera de Contratos y de lo propio de la sesión, devuelve datos."""
    from fastapi.routing import APIRoute
    from main import app
    allowed_prefixes = ("/api/contracts", "/api/auth/me", "/api/health", "/api/auth/logout")
    for headers in (auth_headers_for(rrhh_user), auth_headers_for(rrhh_user, clayton_device.device_id)):
        leaks = []
        for route in app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods or not route.path.startswith("/api/"):
                continue
            if route.path.startswith(allowed_prefixes):
                continue
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            res = client.get(path, headers=headers)
            if res.status_code < 300:
                leaks.append((route.path, res.status_code))
        # Públicas a propósito (menú del cliente, enlaces de pago, medios): no dependen de la sesión.
        leaks = [l for l in leaks if not l[0].startswith(("/api/menu", "/api/orders/public", "/api/payments", "/api/media", "/api/webhooks", "/api/branches"))]
        assert leaks == [], leaks


def test_rrhh_password_must_be_strong(client, admin_user):
    headers = auth_headers_for(admin_user)
    base = {"username": "venus2", "name": "Venus Dos", "role": "rrhh", "active": True}
    assert client.post("/api/users/", json={**base, "password": "2901"}, headers=headers).status_code == 422
    assert client.post("/api/users/", json={**base, "password": "1234567890"}, headers=headers).status_code == 422
    assert client.post("/api/users/", json={**base, "password": "corta-1"}, headers=headers).status_code == 422
    ok = client.post("/api/users/", json={**base, "password": "una-clave-larga-123"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["role"] == "rrhh" and ok.json()["branch_id"] is None


def test_word_contract_lists_each_dependent_in_its_own_paragraph(tmp_path):
    """El contrato en Word: cada dependiente va en su propio párrafo (en el justificado se estiraba el espacio) y cierra bien."""
    node = shutil.which("node")
    docx = pytest.importorskip("docx")
    if not node:
        pytest.skip("Node no está instalado")
    builder = Path(__file__).resolve().parents[2] / "frontend" / "js" / "components" / "contract_docx.js"
    out = tmp_path / "c.docx"
    script = tmp_path / "make.js"
    script.write_text(
        "globalThis.window = globalThis; require(%r); const CD = globalThis.ContractDocx;\n"
        "const base = {nombre:'Ana', apellido:'Pérez', genero:'F', nacionalidad:'Panameña', numId:'8-100-200', puesto:'Cajera',"
        " inicio:'2026-10-12', fin:'2027-04-12', salario:650, funciones:'Función uno,'};\n"
        "const deps = [{nombre:'Mateo Vega', edad:'4', parentesco:'Hijo'}, {nombre:'Carmen Salazar', edad:'', parentesco:'Madre'}];\n"
        "const fs = require('fs');\n"
        "CD.buildDocx(Object.assign({}, base, {tieneDep:true, dependientes:deps})).arrayBuffer()"
        ".then(b => { fs.writeFileSync(%r, Buffer.from(b)); return CD.buildDocx(Object.assign({}, base, {tieneDep:false, dependientes:[]})).arrayBuffer(); })"
        ".then(b => fs.writeFileSync(%r, Buffer.from(b)));"
        % (str(builder), str(out), str(tmp_path / "sin.docx")), encoding="utf-8")
    run = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    texts = [p.text for p in docx.Document(str(out)).paragraphs]
    i = next(k for k, t in enumerate(texts) if t.startswith("DÉCIMO"))
    assert texts[i].endswith("que sí tiene dependientes:")
    assert texts[i + 1] == "Mateo Vega, 4 años, Hijo;" and texts[i + 2] == "Carmen Salazar, Madre."
    assert "\n" not in texts[i] and "\t" not in texts[i]                    # sin saltos dentro del párrafo justificado
    sin = [p.text for p in docx.Document(str(tmp_path / "sin.docx")).paragraphs]
    j = next(k for k, t in enumerate(sin) if t.startswith("DÉCIMO"))
    assert sin[j].endswith("que no tiene dependientes.") and sin[j + 1].startswith("En fe de lo pactado")
