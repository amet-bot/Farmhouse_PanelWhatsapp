from tests.conftest import auth_headers_for


def _headers(user, device):
    return auth_headers_for(user, device.device_id)


def test_directory_crosses_branches(client, clayton_agent, clayton_device, obarrio_agent, admin_user):
    """
    El punto del sistema: un agente de Clayton ve al de Obarrio. /users/ no sirve para esto
    porque a un agente le recorta la lista a su propia sucursal.
    """
    res = client.get("/api/internal/directory", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 200, res.text
    names = {u["name"] for u in res.json()}
    assert "Agente Obarrio" in names
    assert "Admin General" in names
    assert "Agente Clayton" not in names  # uno mismo nunca aparece


def test_directory_hides_inactive_people(client, clayton_agent, clayton_device, obarrio_agent, db_session):
    obarrio_agent.active = False
    db_session.commit()
    res = client.get("/api/internal/directory", headers=_headers(clayton_agent, clayton_device))
    assert "Agente Obarrio" not in {u["name"] for u in res.json()}


def test_branch_channel_is_created_with_every_active_member(client, clayton_agent, clayton_device, supervisor_user):
    """El canal del equipo aparece solo, con todos los activos de esa sucursal dentro."""
    res = client.get("/api/internal/threads", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 200, res.text
    channels = [t for t in res.json() if t["kind"] == "branch"]
    assert len(channels) == 1
    assert channels[0]["title"] == "Equipo Clayton"
    # El agente y el supervisor, ambos de Clayton.
    assert channels[0]["member_count"] == 2


def test_admin_without_branch_has_no_team_channel(client, admin_user):
    res = client.get("/api/internal/threads", headers=auth_headers_for(admin_user))
    assert res.status_code == 200
    assert [t for t in res.json() if t["kind"] == "branch"] == []


def test_direct_thread_is_idempotent(client, clayton_agent, clayton_device, obarrio_agent):
    first = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                        headers=_headers(clayton_agent, clayton_device))
    assert first.status_code == 201, first.text
    assert first.json()["title"] == "Agente Obarrio"

    second = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device))
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]  # no se duplica el hilo


def test_cannot_open_a_thread_with_yourself(client, clayton_agent, clayton_device):
    res = client.post("/api/internal/threads/direct", json={"user_id": clayton_agent.id},
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 400


def test_message_round_trip_between_branches(client, clayton_agent, clayton_device, obarrio_agent, obarrio_device):
    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()

    sent = client.post(f"/api/internal/threads/{thread['id']}/messages",
                       json={"body": "¿Les queda açaí para prestarnos?"},
                       headers=_headers(clayton_agent, clayton_device))
    assert sent.status_code == 201, sent.text
    assert sent.json()["sender_name"] == "Agente Clayton"

    # El de Obarrio lo ve en su hilo, y le cuenta como no leído.
    inbox = client.get("/api/internal/threads", headers=_headers(obarrio_agent, obarrio_device)).json()
    mine = next(t for t in inbox if t["id"] == thread["id"])
    assert mine["unread_count"] == 1
    assert mine["last_message_preview"] == "¿Les queda açaí para prestarnos?"
    assert mine["title"] == "Agente Clayton"

    msgs = client.get(f"/api/internal/threads/{thread['id']}/messages",
                      headers=_headers(obarrio_agent, obarrio_device)).json()
    assert [m["body"] for m in msgs] == ["¿Les queda açaí para prestarnos?"]


def test_sender_does_not_see_own_message_as_unread(client, clayton_agent, clayton_device, obarrio_agent):
    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()
    client.post(f"/api/internal/threads/{thread['id']}/messages", json={"body": "hola"},
                headers=_headers(clayton_agent, clayton_device))

    inbox = client.get("/api/internal/threads", headers=_headers(clayton_agent, clayton_device)).json()
    assert next(t for t in inbox if t["id"] == thread["id"])["unread_count"] == 0


def test_marking_read_clears_the_counter(client, clayton_agent, clayton_device, obarrio_agent, obarrio_device):
    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()
    client.post(f"/api/internal/threads/{thread['id']}/messages", json={"body": "uno"},
                headers=_headers(clayton_agent, clayton_device))

    read = client.post(f"/api/internal/threads/{thread['id']}/read",
                       headers=_headers(obarrio_agent, obarrio_device))
    assert read.status_code == 204

    inbox = client.get("/api/internal/threads", headers=_headers(obarrio_agent, obarrio_device)).json()
    assert next(t for t in inbox if t["id"] == thread["id"])["unread_count"] == 0


def test_outsider_cannot_read_or_write_a_thread(client, clayton_agent, clayton_device, obarrio_agent,
                                                obarrio_device, supervisor_user):
    """Un tercero no entra a un hilo ajeno aunque sea supervisor de una de las dos sucursales."""
    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()

    spy = _headers(supervisor_user, clayton_device)
    assert client.get(f"/api/internal/threads/{thread['id']}/messages", headers=spy).status_code == 403
    assert client.post(f"/api/internal/threads/{thread['id']}/messages", json={"body": "cuchicheo"},
                       headers=spy).status_code == 403
    assert client.post(f"/api/internal/threads/{thread['id']}/read", headers=spy).status_code == 403


def test_empty_message_is_rejected(client, clayton_agent, clayton_device, obarrio_agent):
    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()
    res = client.post(f"/api/internal/threads/{thread['id']}/messages", json={"body": "   "},
                      headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 400


def test_team_channel_reaches_everyone_in_the_branch(client, clayton_agent, clayton_device,
                                                     supervisor_user):
    inbox = client.get("/api/internal/threads", headers=_headers(clayton_agent, clayton_device)).json()
    channel = next(t for t in inbox if t["kind"] == "branch")

    client.post(f"/api/internal/threads/{channel['id']}/messages",
                json={"body": "Llegó el cargamento, falta una caja de envases."},
                headers=_headers(clayton_agent, clayton_device))

    other = client.get("/api/internal/threads", headers=_headers(supervisor_user, clayton_device)).json()
    mine = next(t for t in other if t["id"] == channel["id"])
    assert mine["unread_count"] == 1
    assert mine["last_message_sender"] == "Agente"


def test_new_message_is_pushed_to_every_participant(client, clayton_agent, clayton_device,
                                                    obarrio_agent, monkeypatch):
    """
    El aviso en vivo sale hacia TODOS los del hilo, incluido quien escribe (que puede tener
    otra pestaña abierta). Se difunde uno por uno y no por sala de sucursal: un hilo directo
    cruza sucursales, así que la regla de audiencia por sucursal no aplica.
    """
    from routers import internal_chat

    enviados = []

    async def _spy(message, user_id):
        enviados.append((user_id, message))

    monkeypatch.setattr(internal_chat.ws_manager, "send_personal_message", _spy)

    thread = client.post("/api/internal/threads/direct", json={"user_id": obarrio_agent.id},
                         headers=_headers(clayton_agent, clayton_device)).json()
    client.post(f"/api/internal/threads/{thread['id']}/messages", json={"body": "probando"},
                headers=_headers(clayton_agent, clayton_device))

    assert {uid for uid, _ in enviados} == {clayton_agent.id, obarrio_agent.id}
    _, evento = enviados[0]
    assert evento["type"] == "internal_message"
    assert evento["thread_id"] == thread["id"]
    assert evento["message"]["body"] == "probando"
