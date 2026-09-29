"""
Notificaciones nativas de la app de Android (Firebase): registrar el celular, mandarle los avisos
de siempre por el canal "a la vista" y olvidar los celulares que desinstalaron la app.
"""
import pytest

from models.native_push import NativePushToken
from services import fcm_service, push_service
from tests.conftest import auth_headers_for

TOKEN = "fcm-token-de-prueba-" + "x" * 40


@pytest.fixture
def fcm(monkeypatch):
    """Firebase "configurado": lo que se mandaría queda anotado en vez de salir a Google."""
    enviados = []
    monkeypatch.setattr(fcm_service, "is_configured", lambda: True)

    def send(token, title, body, url=None, tag=None):
        if token.startswith("muerto"):
            raise fcm_service.TokenInvalido("UNREGISTERED")
        enviados.append({"token": token, "title": title, "body": body, "url": url, "tag": tag})

    monkeypatch.setattr(fcm_service, "send", send)
    return enviados


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def test_registrar_y_quitar_el_celular(client, db_session, supervisor_user, clayton_agent, clayton_device):
    h = _h(supervisor_user, clayton_device)
    assert client.post("/api/push/native/register", json={"token": TOKEN}, headers=h).json()["status"] == "registered"
    assert db_session.query(NativePushToken).filter_by(token=TOKEN).one().user_id == supervisor_user.id

    # El mismo celular, con otra persona: pasa a esa persona (teléfono compartido de la sucursal).
    otro = _h(clayton_agent, clayton_device)
    assert client.post("/api/push/native/register", json={"token": TOKEN}, headers=otro).json()["status"] == "updated"
    db_session.expire_all()
    assert db_session.query(NativePushToken).filter_by(token=TOKEN).one().user_id == clayton_agent.id

    client.post("/api/push/native/unregister", json={"token": TOKEN}, headers=otro)
    assert db_session.query(NativePushToken).count() == 0


def test_sin_firebase_la_prueba_lo_dice(client, supervisor_user, clayton_device, monkeypatch):
    monkeypatch.setattr(fcm_service, "is_configured", lambda: False)
    h = _h(supervisor_user, clayton_device)
    assert client.get("/api/push/native/status", headers=h).json() == {"enabled": False}
    assert client.post("/api/push/native/test", headers=h).status_code == 503


def test_notificacion_de_prueba(client, supervisor_user, clayton_device, fcm):
    h = _h(supervisor_user, clayton_device)
    assert client.post("/api/push/native/test", headers=h).status_code == 404   # todavía sin celular
    client.post("/api/push/native/register", json={"token": TOKEN}, headers=h)
    assert client.post("/api/push/native/test", headers=h).json() == {"sent": 1}
    assert fcm[0]["token"] == TOKEN and fcm[0]["url"] == "/hub"


def test_los_avisos_de_la_sucursal_llegan_a_la_app(db_session, clayton_branch, supervisor_user, clayton_agent, fcm):
    db_session.add(NativePushToken(user_id=supervisor_user.id, token=TOKEN))
    db_session.add(NativePushToken(user_id=clayton_agent.id, token="agente-" + "y" * 40))
    db_session.commit()

    # Solo encargados: la cocina no recibe el aviso de diferencias.
    push_service.notify_branch_staff(db_session, clayton_branch.id, "Cargamento con diferencias · Clayton",
                                     "Tomate: faltó 2 kg", "/inventario?view=cargamentos&shipment=1",
                                     tag="fh-shipment-1", managers_only=True)
    assert [e["token"] for e in fcm] == [TOKEN]
    assert fcm[0]["url"] == "/inventario?view=cargamentos&shipment=1" and fcm[0]["tag"] == "fh-shipment-1"

    # Un aviso para toda la sucursal le llega también a la cocina.
    fcm.clear()
    push_service.notify_branch_staff(db_session, clayton_branch.id, "Hoy llega PriceSmart", "Clayton", "/inventario")
    assert sorted(e["token"][:6] for e in fcm) == sorted([TOKEN[:6], "agente"])


def test_el_celular_que_desinstalo_la_app_se_olvida(db_session, clayton_branch, supervisor_user, fcm):
    db_session.add(NativePushToken(user_id=supervisor_user.id, token="muerto-" + "z" * 40))
    db_session.commit()
    push_service.notify_branch_staff(db_session, clayton_branch.id, "Aviso", "texto", "/hub", managers_only=True)
    assert db_session.query(NativePushToken).count() == 0


def test_el_mensaje_va_por_el_canal_a_la_vista():
    m = fcm_service.build_message(TOKEN, "Merma importante · Clayton", "Salmón: se pierden $25", "/inventario?view=merma&waste=3", "fh-waste-3")["message"]
    assert m["token"] == TOKEN
    assert m["android"]["priority"] == "HIGH"
    n = m["android"]["notification"]
    assert (n["channel_id"], n["visibility"], n["notification_priority"]) == ("avisos", "PUBLIC", "PRIORITY_HIGH")
    assert n["icon"] == "ic_stat_farmhouse" and n["tag"] == "fh-waste-3"
    assert m["data"]["url"] == "/inventario?view=merma&waste=3"
