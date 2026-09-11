"""
Aislamiento de notificaciones por rol y sucursal.

Estas pruebas cubren la capa de NOTIFICACIONES (WebSocket + Web Push), que es distinta del
aislamiento del listado REST que ya cubre test_branch_isolation.py. El fallo original era
justamente esa asimetría: un agente de Clayton no podía *listar* las conversaciones de
Obarrio, pero sí recibía sus notificaciones en tiempo real con el nombre del contacto y el
contenido del mensaje dentro del payload.

Las difusiones son corrutinas y el proyecto no usa pytest-asyncio, así que se ejecutan con
el ayudante `run()` (asyncio.run) para no agregar una dependencia nueva a la suite.
"""

import asyncio
import json

import pytest

from config import settings
from models.contact import Contact
from models.conversation import Conversation
from models.push_subscription import PushSubscription
from models.user import User
from security.auth import get_password_hash
from services import push_service
from services.notification_audience import can_receive_branch_event
from services.websocket_manager import ConnectionManager
from tests.conftest import auth_headers_for


def run(coro):
    """Ejecuta una corrutina de difusión dentro de una prueba sincrónica."""
    return asyncio.run(coro)


class FakeWebSocket:
    """Doble de prueba de un WebSocket: guarda todo lo que se le envía."""

    def __init__(self, label: str):
        self.label = label
        self.sent: list = []

    async def send_text(self, text_data: str) -> None:
        self.sent.append(json.loads(text_data))

    def types(self) -> list:
        return [m.get("type") for m in self.sent]

    def __repr__(self) -> str:  # pragma: no cover - solo para leer fallos
        return f"<FakeWebSocket {self.label}>"


@pytest.fixture
def global_supervisor(db_session):
    """Supervisor global: rol supervisor SIN sucursal asignada."""
    user = User(
        id=71,
        username="supervisor_global",
        name="Supervisor Global",
        email="supervisor.global@farmhouse.pa",
        password_hash=get_password_hash("Supervisor123!"),
        role="supervisor",
        branch_id=None,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def panel(clayton_branch, obarrio_branch, admin_user, clayton_agent, obarrio_agent,
          supervisor_user, global_supervisor):
    """
    Un ConnectionManager con una conexión por perfil, ya registradas.

    `supervisor_user` viene de conftest y está asignado a Clayton, así que sirve de
    supervisor local; `global_supervisor` es el que no tiene sucursal.
    """
    manager = ConnectionManager()
    sockets = {}

    perfiles = [
        ("admin", admin_user, None),
        ("agente_clayton", clayton_agent, clayton_branch.id),
        ("agente_obarrio", obarrio_agent, obarrio_branch.id),
        ("supervisor_clayton", supervisor_user, clayton_branch.id),
        ("supervisor_global", global_supervisor, None),
    ]

    async def _register_all():
        for label, user, branch_id in perfiles:
            ws = FakeWebSocket(label)
            sockets[label] = ws
            await manager.connect(ws, user_id=user.id, branch_id=branch_id, role=user.role)

    run(_register_all())
    return manager, sockets, clayton_branch.id, obarrio_branch.id


# ---------------------------------------------------------------------------
# 1. La regla de audiencia, aislada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "role,user_branch,conv_branch,expected",
    [
        # Administrador: todas las sucursales y también las conversaciones sin sucursal.
        ("admin", None, 1, True),
        ("admin", None, 2, True),
        ("admin", None, None, True),
        # Agente de la sucursal 1.
        ("agent", 1, 1, True),
        ("agent", 1, 2, False),
        ("agent", 1, None, False),
        # Supervisor global (sin sucursal): todo.
        ("supervisor", None, 1, True),
        ("supervisor", None, 2, True),
        ("supervisor", None, None, True),
        # Supervisor asignado a la sucursal 1: solo la 1, y nada sin sucursal.
        ("supervisor", 1, 1, True),
        ("supervisor", 1, 2, False),
        ("supervisor", 1, None, False),
    ],
)
def test_regla_de_audiencia(role, user_branch, conv_branch, expected):
    assert can_receive_branch_event(role, user_branch, conv_branch) is expected


# ---------------------------------------------------------------------------
# 2. WebSocket en tiempo real
# ---------------------------------------------------------------------------

def test_admin_recibe_de_todas_las_sucursales(panel):
    manager, sockets, clayton_id, obarrio_id = panel

    run(manager.broadcast_to_branch(clayton_id, {"type": "msg_clayton"}))
    run(manager.broadcast_to_branch(obarrio_id, {"type": "msg_obarrio"}))

    assert sockets["admin"].types() == ["msg_clayton", "msg_obarrio"]


def test_agente_de_clayton_recibe_los_de_clayton(panel):
    manager, sockets, clayton_id, _ = panel

    run(manager.broadcast_to_branch(clayton_id, {"type": "msg_clayton"}))

    assert sockets["agente_clayton"].types() == ["msg_clayton"]


def test_agente_de_clayton_no_recibe_los_de_obarrio(panel):
    manager, sockets, _, obarrio_id = panel

    run(manager.broadcast_to_branch(obarrio_id, {"type": "msg_obarrio"}))

    assert sockets["agente_clayton"].sent == []
    assert sockets["agente_obarrio"].types() == ["msg_obarrio"]


def test_agente_no_recibe_conversaciones_sin_sucursal(panel):
    """
    Una conversación que el bot todavía no enrutó (branch_id nulo) solo se anuncia a quien
    ve global. Esta era la fuga más grande: la rama `if not branch_id` del gestor emitía a
    TODOS los usuarios conectados.
    """
    manager, sockets, _, _ = panel

    run(manager.broadcast_to_branch(None, {"type": "conversacion_sin_sucursal"}))

    assert sockets["agente_clayton"].sent == []
    assert sockets["agente_obarrio"].sent == []
    # Un supervisor de sucursal tampoco: no le corresponde ninguna sucursal todavía.
    assert sockets["supervisor_clayton"].sent == []
    assert sockets["admin"].types() == ["conversacion_sin_sucursal"]
    assert sockets["supervisor_global"].types() == ["conversacion_sin_sucursal"]


def test_supervisor_global_recibe_todas(panel):
    manager, sockets, clayton_id, obarrio_id = panel

    run(manager.broadcast_to_branch(clayton_id, {"type": "msg_clayton"}))
    run(manager.broadcast_to_branch(obarrio_id, {"type": "msg_obarrio"}))
    run(manager.broadcast_to_branch(None, {"type": "msg_sin_sucursal"}))

    assert sockets["supervisor_global"].types() == [
        "msg_clayton",
        "msg_obarrio",
        "msg_sin_sucursal",
    ]


def test_supervisor_local_solo_recibe_su_sucursal(panel):
    manager, sockets, clayton_id, obarrio_id = panel

    run(manager.broadcast_to_branch(clayton_id, {"type": "msg_clayton"}))
    run(manager.broadcast_to_branch(obarrio_id, {"type": "msg_obarrio"}))

    assert sockets["supervisor_clayton"].types() == ["msg_clayton"]


def test_no_hay_notificaciones_duplicadas_en_transferencia(panel):
    """
    Una transferencia concierne a dos sucursales. Emitir una vez por sucursal (el bucle
    `for room_branch_id in {old, new}` original) entregaba el evento DOS veces a los
    admins y supervisores globales, porque califican en ambas.
    """
    manager, sockets, clayton_id, obarrio_id = panel

    run(manager.broadcast_to_branches(
        {clayton_id, obarrio_id}, {"type": "conversation_transferred"}
    ))

    assert sockets["admin"].types() == ["conversation_transferred"]
    assert sockets["supervisor_global"].types() == ["conversation_transferred"]
    # Y cada sucursal involucrada lo recibe una sola vez.
    assert sockets["agente_clayton"].types() == ["conversation_transferred"]
    assert sockets["agente_obarrio"].types() == ["conversation_transferred"]


def test_broadcast_a_visores_globales_excluye_sucursales(panel):
    manager, sockets, _, _ = panel

    run(manager.broadcast_to_global_viewers({"type": "evento_administrativo"}))

    assert sockets["admin"].types() == ["evento_administrativo"]
    assert sockets["supervisor_global"].types() == ["evento_administrativo"]
    assert sockets["agente_clayton"].sent == []
    assert sockets["agente_obarrio"].sent == []
    assert sockets["supervisor_clayton"].sent == []


def test_un_agente_nunca_escala_a_visor_global(panel):
    """
    El rol y la sucursal se fijan en el handshake desde la base de datos
    (routers/websocket.py), no desde un parámetro del cliente, y el filtrado usa esa
    identidad guardada. Un agente, aunque tenga varias conexiones abiertas, nunca alcanza
    los eventos sin sucursal, que son los de visibilidad global.
    """
    manager, sockets, _, obarrio_id = panel

    otra_pestana = FakeWebSocket("agente_obarrio_2")
    run(manager.connect(otra_pestana, user_id=3, branch_id=obarrio_id, role="agent"))

    run(manager.broadcast_to_branch(None, {"type": "sin_sucursal"}))
    assert otra_pestana.sent == []
    assert sockets["agente_obarrio"].sent == []

    # Y lo de su propia sucursal sí llega a las dos pestañas, una vez a cada una.
    run(manager.broadcast_to_branch(obarrio_id, {"type": "msg_obarrio"}))
    assert otra_pestana.types() == ["msg_obarrio"]
    assert sockets["agente_obarrio"].types() == ["msg_obarrio"]


def test_desconectar_saca_la_conexion_de_la_audiencia(panel, clayton_agent):
    manager, sockets, clayton_id, _ = panel
    ws = sockets["agente_clayton"]

    manager.disconnect(ws, user_id=clayton_agent.id, branch_id=clayton_id, role="agent")
    run(manager.broadcast_to_branch(clayton_id, {"type": "msg_clayton"}))

    assert ws.sent == []
    assert ws not in manager.connection_context


# ---------------------------------------------------------------------------
# 3. Web Push
# ---------------------------------------------------------------------------

@pytest.fixture
def push_recorder(monkeypatch):
    """Habilita Web Push y captura a qué usuarios se les habría enviado."""
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "clave-publica-de-prueba")
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "clave-privada-de-prueba")

    enviados = []

    def _fake_send(db, sub, payload):
        enviados.append(sub.user_id)

    monkeypatch.setattr(push_service, "_send_to_subscription", _fake_send)
    return enviados


def _suscribir(db_session, user):
    sub = PushSubscription(
        user_id=user.id,
        endpoint=f"https://push.example/{user.id}",
        p256dh="p256dh-de-prueba",
        auth="auth-de-prueba",
    )
    db_session.add(sub)
    db_session.commit()
    return sub


def test_push_segmenta_por_sucursal(
    db_session, push_recorder, clayton_branch, obarrio_branch,
    admin_user, clayton_agent, obarrio_agent, supervisor_user, global_supervisor
):
    for user in (admin_user, clayton_agent, obarrio_agent, supervisor_user, global_supervisor):
        _suscribir(db_session, user)

    push_service.notify_branch_new_message(
        db_session, clayton_branch.id, "Farmhouse", "Nuevo mensaje", conversation_id=1
    )

    destinatarios = set(push_recorder)
    assert admin_user.id in destinatarios
    assert clayton_agent.id in destinatarios
    assert supervisor_user.id in destinatarios          # supervisor de Clayton
    assert global_supervisor.id in destinatarios        # supervisor global
    assert obarrio_agent.id not in destinatarios        # otra sucursal


def test_push_de_otra_sucursal_no_llega_al_agente_de_clayton(
    db_session, push_recorder, obarrio_branch, clayton_agent, obarrio_agent, supervisor_user
):
    for user in (clayton_agent, obarrio_agent, supervisor_user):
        _suscribir(db_session, user)

    push_service.notify_branch_new_message(
        db_session, obarrio_branch.id, "Farmhouse", "Nuevo mensaje", conversation_id=2
    )

    destinatarios = set(push_recorder)
    assert obarrio_agent.id in destinatarios
    assert clayton_agent.id not in destinatarios
    # El supervisor asignado a Clayton tampoco recibe lo de Obarrio.
    assert supervisor_user.id not in destinatarios


def test_push_sin_sucursal_solo_a_visores_globales(
    db_session, push_recorder, clayton_agent, obarrio_agent,
    admin_user, supervisor_user, global_supervisor
):
    for user in (clayton_agent, obarrio_agent, admin_user, supervisor_user, global_supervisor):
        _suscribir(db_session, user)

    push_service.notify_branch_new_message(
        db_session, None, "Farmhouse", "Conversación nueva", conversation_id=3
    )

    destinatarios = set(push_recorder)
    assert destinatarios == {admin_user.id, global_supervisor.id}


def test_push_no_duplica_por_usuario(
    db_session, push_recorder, clayton_branch, admin_user, clayton_agent
):
    _suscribir(db_session, admin_user)
    _suscribir(db_session, clayton_agent)

    push_service.notify_branch_new_message(
        db_session, clayton_branch.id, "Farmhouse", "Nuevo mensaje", conversation_id=4
    )

    # El admin califica por rol y el agente por sucursal: una notificación cada uno.
    assert sorted(push_recorder) == sorted([admin_user.id, clayton_agent.id])


# ---------------------------------------------------------------------------
# 4. Transferencia real por la API: cambian los destinatarios
# ---------------------------------------------------------------------------

def test_transferencia_cambia_los_destinatarios(
    client, db_session, monkeypatch, clayton_branch, obarrio_branch,
    admin_user, clayton_agent, obarrio_agent
):
    """
    Requisito 7: tras transferir, los agentes de la sucursal anterior dejan de recibir las
    notificaciones de esa conversación y los de la nueva empiezan a recibirlas.
    """
    contact = Contact(name="Cliente Transferido", phone="+50769998877")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(
        customer_id=contact.id,
        branch_id=clayton_branch.id,
        status="open",
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    manager = ConnectionManager()
    ws_clayton = FakeWebSocket("agente_clayton")
    ws_obarrio = FakeWebSocket("agente_obarrio")

    async def _connect_both():
        await manager.connect(ws_clayton, user_id=clayton_agent.id,
                              branch_id=clayton_branch.id, role="agent")
        await manager.connect(ws_obarrio, user_id=obarrio_agent.id,
                              branch_id=obarrio_branch.id, role="agent")

    run(_connect_both())

    # El endpoint de transferencia usa el singleton ws_manager: se sustituye por el gestor
    # de prueba para poder observar a quién le llegó el evento.
    monkeypatch.setattr("routers.conversations.ws_manager", manager)

    # Antes de transferir, la conversación es de Clayton.
    run(manager.broadcast_to_branch(conv.branch_id, {"type": "antes"}))
    assert ws_clayton.types() == ["antes"]
    assert ws_obarrio.sent == []

    response = client.post(
        f"/api/conversations/{conv.id}/transfer",
        json={"target_branch_id": obarrio_branch.id, "reason": "Cliente de Obarrio"},
        headers=auth_headers_for(admin_user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["branch_id"] == obarrio_branch.id

    # Ambas sucursales reciben el aviso de la transferencia, UNA sola vez cada una.
    assert ws_clayton.types() == ["antes", "conversation_transferred"]
    assert ws_obarrio.types() == ["conversation_transferred"]

    # Y de aquí en adelante los mensajes de esa conversación son de Obarrio.
    db_session.refresh(conv)
    ws_clayton.sent.clear()
    ws_obarrio.sent.clear()
    run(manager.broadcast_to_branch(conv.branch_id, {"type": "despues"}))

    assert ws_clayton.sent == []
    assert ws_obarrio.types() == ["despues"]
