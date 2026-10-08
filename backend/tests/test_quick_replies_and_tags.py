"""
Respuestas rápidas configurables y etiquetas de cliente del Centro WhatsApp.
"""
from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation
from models.audit import AuditEvent


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


# ----------------------------------------------------------------------------- respuestas rápidas

def test_agente_ve_globales_y_de_su_sucursal_pero_no_administra(client, admin_user, clayton_device, clayton_agent, clayton_branch, obarrio_branch):
    ha = _h(admin_user, clayton_device)
    assert client.post("/api/quick-replies/", json={"shortcut": "Horario", "title": "Horario", "body": "Abrimos 8 AM"}, headers=ha).status_code == 200
    assert client.post("/api/quick-replies/", json={"shortcut": "cly", "title": "Solo Clayton", "body": "x", "branch_id": clayton_branch.id}, headers=ha).status_code == 200
    assert client.post("/api/quick-replies/", json={"shortcut": "obr", "title": "Solo Obarrio", "body": "x", "branch_id": obarrio_branch.id}, headers=ha).status_code == 200
    inactiva = client.post("/api/quick-replies/", json={"shortcut": "vieja", "title": "Inactiva", "body": "x", "active": False}, headers=ha).json()

    lista = client.get("/api/quick-replies/", headers=_h(clayton_agent, clayton_device)).json()
    assert {r["shortcut"] for r in lista} == {"horario", "cly"}   # el atajo se normaliza a minúsculas
    # El agente no crea ni borra.
    assert client.post("/api/quick-replies/", json={"shortcut": "z", "title": "z", "body": "z"}, headers=_h(clayton_agent, clayton_device)).status_code == 403
    assert client.delete(f"/api/quick-replies/{inactiva['id']}", headers=_h(clayton_agent, clayton_device)).status_code == 403
    # El admin ve todo, incluidas inactivas.
    todas = client.get("/api/quick-replies/?include_inactive=1", headers=ha).json()
    assert {r["shortcut"] for r in todas} == {"horario", "cly", "obr", "vieja"}


def test_atajo_no_se_repite_y_se_valida(client, admin_user, clayton_device, clayton_branch):
    ha = _h(admin_user, clayton_device)
    assert client.post("/api/quick-replies/", json={"shortcut": "/menu", "title": "Menú", "body": "{menu}"}, headers=ha).json()["shortcut"] == "menu"
    # Global repetido: no. En una sucursal el mismo atajo tampoco (al escribir /menu habría dos).
    assert client.post("/api/quick-replies/", json={"shortcut": "menu", "title": "Otro", "body": "x"}, headers=ha).status_code == 400
    assert client.post("/api/quick-replies/", json={"shortcut": "menu", "title": "Otro", "body": "x", "branch_id": clayton_branch.id}, headers=ha).status_code == 400
    assert client.post("/api/quick-replies/", json={"shortcut": "con espacios!", "title": "x", "body": "x"}, headers=ha).status_code == 422


def test_supervisor_de_sucursal_solo_administra_las_suyas(client, admin_user, clayton_device, supervisor_user, clayton_branch, obarrio_branch):
    ha = _h(admin_user, clayton_device)
    hs = _h(supervisor_user, clayton_device)
    global_ = client.post("/api/quick-replies/", json={"shortcut": "g", "title": "Global", "body": "x"}, headers=ha).json()
    ajena = client.post("/api/quick-replies/", json={"shortcut": "o", "title": "Obarrio", "body": "x", "branch_id": obarrio_branch.id}, headers=ha).json()

    # Aunque pida global o de Obarrio, la suya queda en Clayton.
    mia = client.post("/api/quick-replies/", json={"shortcut": "s", "title": "Sup", "body": "x", "branch_id": obarrio_branch.id}, headers=hs)
    assert mia.status_code == 200 and mia.json()["branch_id"] == clayton_branch.id
    assert client.put(f"/api/quick-replies/{global_['id']}", json={"title": "Hack"}, headers=hs).status_code == 403
    assert client.put(f"/api/quick-replies/{ajena['id']}", json={"title": "Hack"}, headers=hs).status_code == 403
    assert client.put(f"/api/quick-replies/{mia.json()['id']}", json={"branch_id": None}, headers=hs).status_code == 403
    assert client.put(f"/api/quick-replies/{mia.json()['id']}", json={"title": "Mejor"}, headers=hs).status_code == 200
    assert client.delete(f"/api/quick-replies/{global_['id']}", headers=hs).status_code == 403
    assert client.delete(f"/api/quick-replies/{mia.json()['id']}", headers=hs).status_code == 204


# ----------------------------------------------------------------------------- etiquetas

def _contacto_en(db_session, branch, phone="+50760000001", name="Ana"):
    c = Contact(name=name, phone=phone)
    db_session.add(c)
    db_session.commit()
    db_session.add(Conversation(customer_id=c.id, branch_id=branch.id, status="open"))
    db_session.commit()
    return c


def test_crear_etiquetas_y_ponerlas_al_cliente(client, db_session, admin_user, clayton_device, clayton_agent, clayton_branch):
    ha = _h(admin_user, clayton_device)
    vip = client.post("/api/tags/", json={"name": " VIP ", "color": "#F59E0B"}, headers=ha)
    assert vip.status_code == 200 and vip.json()["name"] == "VIP" and vip.json()["color"] == "#f59e0b"
    corp = client.post("/api/tags/", json={"name": "Corporativo"}, headers=ha).json()
    assert client.post("/api/tags/", json={"name": "vip"}, headers=ha).status_code == 200  # distinto por mayúsculas: se permite
    assert client.post("/api/tags/", json={"name": "VIP"}, headers=ha).status_code == 400
    assert client.post("/api/tags/", json={"name": "Mal color", "color": "rojo"}, headers=ha).status_code == 422
    # Un agente no crea etiquetas, pero sí las ve y las asigna.
    hag = _h(clayton_agent, clayton_device)
    assert client.post("/api/tags/", json={"name": "X"}, headers=hag).status_code == 403
    assert len(client.get("/api/tags/", headers=hag).json()) == 3

    c = _contacto_en(db_session, clayton_branch)
    res = client.put(f"/api/tags/contacts/{c.id}", json={"tag_ids": [vip.json()["id"], corp["id"]]}, headers=hag)
    assert res.status_code == 200
    assert [t["name"] for t in res.json()["tags"]] == ["Corporativo", "VIP"]

    # Se ve en la lista de conversaciones (ContactResponse.tags) y en el contacto.
    convs = client.get("/api/conversations/", headers=hag).json()
    conv = next(x for x in convs if x["contact"]["id"] == c.id)
    assert {t["name"] for t in conv["contact"]["tags"]} == {"VIP", "Corporativo"}
    assert {t["name"] for t in client.get(f"/api/contacts/{c.id}", headers=hag).json()["tags"]} == {"VIP", "Corporativo"}

    # Reemplazar el conjunto; etiqueta inexistente → 400; auditoría de cambios.
    assert client.put(f"/api/tags/contacts/{c.id}", json={"tag_ids": [corp["id"]]}, headers=hag).json()["tags"][0]["name"] == "Corporativo"
    assert client.put(f"/api/tags/contacts/{c.id}", json={"tag_ids": [9999]}, headers=hag).status_code == 400
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "contact.tags_changed", AuditEvent.entity_id == c.id).count() == 2


def test_agente_no_etiqueta_clientes_de_otra_sucursal(client, db_session, clayton_device, clayton_agent, obarrio_branch, admin_user):
    tag = client.post("/api/tags/", json={"name": "Reclamo", "color": "#ef4444"}, headers=_h(admin_user, clayton_device)).json()
    c = _contacto_en(db_session, obarrio_branch, phone="+50760000002", name="Beto")
    assert client.put(f"/api/tags/contacts/{c.id}", json={"tag_ids": [tag["id"]]}, headers=_h(clayton_agent, clayton_device)).status_code == 403


def test_borrar_etiqueta_la_quita_de_los_clientes(client, db_session, admin_user, clayton_device, clayton_branch):
    ha = _h(admin_user, clayton_device)
    tag = client.post("/api/tags/", json={"name": "Temporal"}, headers=ha).json()
    c = _contacto_en(db_session, clayton_branch, phone="+50760000003", name="Caro")
    client.put(f"/api/tags/contacts/{c.id}", json={"tag_ids": [tag["id"]]}, headers=ha)
    renombrada = client.put(f"/api/tags/{tag['id']}", json={"name": "Frecuente", "color": "#2563eb"}, headers=ha).json()
    assert renombrada["name"] == "Frecuente"
    assert client.delete(f"/api/tags/{tag['id']}", headers=ha).status_code == 204
    db_session.expire_all()
    assert client.get(f"/api/contacts/{c.id}", headers=ha).json()["tags"] == []
    assert client.get("/api/tags/", headers=ha).json() == []
