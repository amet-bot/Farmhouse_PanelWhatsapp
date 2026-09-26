"""
Regresiones de la auditoría de estabilización (bloque 1: núcleo y seguridad del backend).
"""
from pathlib import Path

from tests.conftest import auth_headers_for
from models.user import User
from models.push_subscription import PushSubscription
from models.message import Message
from models.contact import Contact
from models.conversation import Conversation
from routers import auth as auth_router
from routers.media import MEDIA_DIR


def _second_admin(db_session):
    admin2 = User(id=10, username="admin2", name="Admin Dos", email="admin2@farmhouse.pa",
                  password_hash="x", role="admin", active=True)
    db_session.add(admin2)
    db_session.commit()
    return admin2


# ---- PUT /users/{id}: mismas protecciones que toggle-active y delete ----

def test_update_cannot_demote_or_deactivate_self(client, admin_user, db_session):
    _second_admin(db_session)
    headers = auth_headers_for(admin_user)
    res = client.put(f"/api/users/{admin_user.id}", json={"role": "agent"}, headers=headers)
    assert res.status_code == 400
    res = client.put(f"/api/users/{admin_user.id}", json={"active": False}, headers=headers)
    assert res.status_code == 400
    db_session.refresh(admin_user)
    assert admin_user.role == "admin" and admin_user.active is True


def test_update_cannot_remove_last_active_admin(client, admin_user, db_session):
    admin2 = _second_admin(db_session)
    admin2.active = False
    db_session.commit()
    # admin2 (inactivo) no cuenta: admin_user es el único activo. Otro admin no puede degradarlo.
    other = User(id=11, username="admin3", name="Admin Tres", password_hash="x", role="admin", active=True)
    db_session.add(other)
    db_session.commit()
    res = client.put(f"/api/users/{admin_user.id}", json={"role": "supervisor"}, headers=auth_headers_for(other))
    assert res.status_code == 200  # queda admin3 activo: permitido
    res = client.put(f"/api/users/{other.id}", json={"active": False}, headers=auth_headers_for(admin_user))
    assert res.status_code == 403  # admin_user ya no es admin, no tiene users.manage


def test_update_ignores_explicit_nulls_on_required_fields(client, admin_user, clayton_agent):
    res = client.put(
        f"/api/users/{clayton_agent.id}",
        json={"username": None, "name": None, "role": None, "active": None},
        headers=auth_headers_for(admin_user),
    )
    assert res.status_code == 200
    assert res.json()["username"] == clayton_agent.username


def test_update_agent_requires_branch(client, admin_user, clayton_agent):
    res = client.put(f"/api/users/{clayton_agent.id}", json={"branch_id": None}, headers=auth_headers_for(admin_user))
    assert res.status_code == 400


def test_update_password_min_length_matches_create(client, admin_user, clayton_agent):
    res = client.put(f"/api/users/{clayton_agent.id}", json={"password": "1234"}, headers=auth_headers_for(admin_user))
    assert res.status_code == 200
    res = client.put(f"/api/users/{clayton_agent.id}", json={"password": "123"}, headers=auth_headers_for(admin_user))
    assert res.status_code == 422


# ---- DELETE /users/{id} con suscripción push ----

def test_delete_user_with_push_subscription(client, admin_user, clayton_agent, db_session):
    db_session.add(PushSubscription(user_id=clayton_agent.id, endpoint="https://push.example/x", p256dh="k", auth="a"))
    db_session.commit()
    res = client.delete(f"/api/users/{clayton_agent.id}", headers=auth_headers_for(admin_user))
    assert res.status_code == 200, res.text
    assert db_session.query(PushSubscription).count() == 0


# ---- Login ----

def test_rate_limit_uses_last_forwarded_hop(client, clayton_agent):
    auth_router._failed_login_attempts.clear()
    try:
        for i in range(auth_router.MAX_FAILED_ATTEMPTS):
            client.post(
                "/api/auth/login",
                json={"username": clayton_agent.username, "password": "incorrecta"},
                headers={"X-Requested-With": "XMLHttpRequest", "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
            )
        # Cambiar la primera IP (la que controla el cliente) ya no genera una clave nueva.
        res = client.post(
            "/api/auth/login",
            json={"username": clayton_agent.username, "password": "incorrecta"},
            headers={"X-Requested-With": "XMLHttpRequest", "X-Forwarded-For": "10.9.9.9, 203.0.113.7"},
        )
        assert res.status_code == 429
    finally:
        auth_router._failed_login_attempts.clear()


# ---- Medios: nada ejecutable se sirve inline desde el origen del panel ----

def _stored_media(db_session, clayton_branch, name: str, mime: str, content: bytes = b"x"):
    path = MEDIA_DIR / name
    path.write_bytes(content)
    contact = Contact(name="Cliente", phone="+50760000001")
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open")
    db_session.add(conv)
    db_session.flush()
    db_session.add(Message(conversation_id=conv.id, direction="incoming", sender_type="customer",
                           media_type="document", content="", media_url=f"/api/media/{name}",
                           media_mime_type=mime))
    db_session.commit()
    return path


def test_html_media_is_downloaded_not_rendered(client, admin_user, db_session, clayton_branch):
    path = _stored_media(db_session, clayton_branch, "audit_test_evil.html", "text/html", b"<script>alert(1)</script>")
    try:
        res = client.get("/api/media/audit_test_evil.html", headers=auth_headers_for(admin_user))
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/octet-stream")
        assert res.headers["content-disposition"].startswith("attachment")
        assert res.headers["content-security-policy"] == "sandbox"
    finally:
        Path(path).unlink(missing_ok=True)


def test_image_media_still_inline(client, admin_user, db_session, clayton_branch):
    path = _stored_media(db_session, clayton_branch, "audit_test_ok.jpg", "image/jpeg")
    try:
        res = client.get("/api/media/audit_test_ok.jpg", headers=auth_headers_for(admin_user))
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")
        assert res.headers["content-disposition"].startswith("inline")
    finally:
        Path(path).unlink(missing_ok=True)


# ---- Seed: no recrea "admin" con la contraseña del repositorio ----

def test_seed_does_not_recreate_admin_when_another_admin_exists(db_session, monkeypatch):
    from seeds import seed_data
    monkeypatch.setattr(seed_data, "SessionLocal", lambda: _NonClosingSession(db_session))
    renamed = User(id=20, username="gerencia", name="Gerencia", password_hash="x", role="admin", active=True)
    db_session.add(renamed)
    db_session.commit()
    seed_data.seed_database()
    assert db_session.query(User).filter(User.username == "admin").first() is None


def test_seed_does_not_reactivate_admin_if_another_is_active(db_session, monkeypatch):
    from seeds import seed_data
    monkeypatch.setattr(seed_data, "SessionLocal", lambda: _NonClosingSession(db_session))
    off = User(id=21, username="admin", name="Viejo", password_hash="x", role="admin", active=False)
    on = User(id=22, username="gerencia", name="Gerencia", password_hash="x", role="admin", active=True)
    db_session.add_all([off, on])
    db_session.commit()
    seed_data.seed_database()
    db_session.refresh(off)
    assert off.active is False


class _NonClosingSession:
    """Envuelve la sesión del test para que el close() del seed no la cierre."""
    def __init__(self, session):
        self._s = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._s, name)
