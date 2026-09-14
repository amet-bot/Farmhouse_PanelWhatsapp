"""
Pruebas del endpoint de la pestaña "Flujo visual": cualquier usuario autenticado puede
consultar el diagrama, pero solo un admin puede guardarlo.
"""
import json

import pytest

from models.bot_flow import BotFlow
from models.user import User
from security.auth import get_password_hash
from tests.conftest import auth_headers_for

SAMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "trigger", "x": 0, "y": 0, "w": 170, "name": "Cliente escribe"},
        {"id": "n2", "type": "message", "x": 0, "y": 120, "w": 260, "name": "Saludo", "text": "hola"},
    ],
    "conns": [{"from": "n1", "fromPort": 0, "to": "n2"}],
}


@pytest.fixture
def agent_user(db_session):
    agent = User(
        id=2,
        username="agente_prueba",
        name="Agente de Prueba",
        email="agente.prueba@farmhouse.pa",
        password_hash=get_password_hash("Prueba123!"),
        role="agent",
        active=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture
def bot_flow(db_session):
    flow = BotFlow(key="main_intake", name="Camino principal (referencia visual)",
                    graph_json=json.dumps(SAMPLE_GRAPH, ensure_ascii=False))
    db_session.add(flow)
    db_session.commit()
    db_session.refresh(flow)
    return flow


def test_cualquier_usuario_autenticado_puede_ver_el_flujo(client, bot_flow, agent_user):
    response = client.get("/api/bot-flows/main_intake", headers=auth_headers_for(agent_user))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["key"] == "main_intake"
    assert len(data["graph"]["nodes"]) == 2


def test_flujo_inexistente_devuelve_404(client, admin_user):
    response = client.get("/api/bot-flows/no-existe", headers=auth_headers_for(admin_user))
    assert response.status_code == 404


def test_agente_no_puede_guardar_el_flujo(client, bot_flow, agent_user):
    new_graph = {"nodes": SAMPLE_GRAPH["nodes"], "conns": []}
    response = client.put(
        "/api/bot-flows/main_intake",
        json={"graph": new_graph},
        headers=auth_headers_for(agent_user),
    )
    assert response.status_code == 403


def test_admin_puede_guardar_el_flujo(client, db_session, bot_flow, admin_user):
    new_graph = {
        "nodes": SAMPLE_GRAPH["nodes"] + [{"id": "n3", "type": "end", "x": 0, "y": 240, "w": 170, "name": "Fin"}],
        "conns": SAMPLE_GRAPH["conns"] + [{"from": "n2", "fromPort": 0, "to": "n3"}],
    }
    response = client.put(
        "/api/bot-flows/main_intake",
        json={"graph": new_graph},
        headers=auth_headers_for(admin_user),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["graph"]["nodes"]) == 3
    assert data["updated_by"] == admin_user.name

    db_session.refresh(bot_flow)
    assert json.loads(bot_flow.graph_json)["nodes"][-1]["id"] == "n3"


def test_guardar_sin_disparador_es_rechazado(client, bot_flow, admin_user):
    graph_sin_trigger = {"nodes": [{"id": "n2", "type": "message", "x": 0, "y": 0, "w": 200, "name": "x", "text": "y"}], "conns": []}
    response = client.put(
        "/api/bot-flows/main_intake",
        json={"graph": graph_sin_trigger},
        headers=auth_headers_for(admin_user),
    )
    assert response.status_code == 400
