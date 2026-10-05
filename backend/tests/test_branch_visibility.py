"""
Sucursales ocultas a los clientes (p. ej. la oficina "Bloc"): existen en el sistema (se les asignan
usuarios, aparecen en Administración) pero no salen en el menú digital ni en el bot de WhatsApp, y no
reciben pedidos públicos. Por defecto toda sucursal es visible: no cambia nada para las existentes.
"""
from models.branch import Branch
from tests.conftest import auth_headers_for
# Los fixtures del bot (ambos se activan en este módulo) y sus ayudas:
from tests.test_bot_nearest_and_fee import _env, listas, PHONE  # noqa: F401
from tests.test_bot_new_flow import _post_bot_message
from tests.test_menu_and_public_orders import BASE_ORDER_PAYLOAD


def _office(db_session, **over):
    data = dict(name="Bloc", code="BLOC", active=True, accepts_delivery=False, visible_to_customers=False,
                latitude=9.0000, longitude=-79.5300)
    data.update(over)
    office = Branch(**data)
    db_session.add(office)
    db_session.commit()
    db_session.refresh(office)
    return office


def test_branches_are_visible_to_customers_by_default(db_session, clayton_branch):
    assert clayton_branch.visible_to_customers is True


def test_public_list_hides_the_office_but_administration_sees_it(client, admin_user, clayton_branch, db_session):
    _office(db_session)
    public = [b["name"] for b in client.get("/api/branches/").json()]
    assert "Clayton" in public and "Bloc" not in public
    admin = {b["name"]: b for b in client.get("/api/branches/admin", headers=auth_headers_for(admin_user)).json()}
    assert admin["Bloc"]["visible_to_customers"] is False and admin["Clayton"]["visible_to_customers"] is True


def test_admin_can_create_a_hidden_branch_and_change_it(client, admin_user):
    headers = auth_headers_for(admin_user)
    created = client.post("/api/branches/", json={"name": "Bloc", "code": "BLOC", "accepts_delivery": False, "visible_to_customers": False}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["visible_to_customers"] is False
    bid = created.json()["id"]
    assert "Bloc" not in [b["name"] for b in client.get("/api/branches/").json()]
    shown = client.put(f"/api/branches/{bid}", json={"visible_to_customers": True}, headers=headers)
    assert shown.status_code == 200 and shown.json()["visible_to_customers"] is True
    assert "Bloc" in [b["name"] for b in client.get("/api/branches/").json()]
    # Omitir el campo al crear la deja visible, como siempre.
    plain = client.post("/api/branches/", json={"name": "Normal", "code": "NOR"}, headers=headers)
    assert plain.json()["visible_to_customers"] is True


def test_only_admin_can_hide_a_branch(client, supervisor_user, clayton_branch, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.put(f"/api/branches/{clayton_branch.id}", json={"visible_to_customers": False}, headers=headers).status_code == 403


def test_public_orders_to_a_hidden_branch_are_rejected(client, clayton_branch, db_session):
    _office(db_session)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    ok = client.post("/api/orders/public", json=BASE_ORDER_PAYLOAD, headers=headers)
    assert ok.status_code == 200, ok.text
    hidden = client.post("/api/orders/public", json={**BASE_ORDER_PAYLOAD, "branch_code": "BLOC"}, headers=headers)
    assert hidden.status_code == 400 and "inválida" in hidden.json()["detail"]


def test_the_bot_never_offers_the_office(client, clayton_branch, obarrio_branch, db_session, listas):
    office = _office(db_session)
    _post_bot_message(client, PHONE, "wamid.V1", text="retiro")
    ids = [r["id"] for r in listas[-1]["rows"]]
    assert f"branch_{clayton_branch.id}" in ids and f"branch_{obarrio_branch.id}" in ids
    assert f"branch_{office.id}" not in ids


def test_the_nearest_branch_ignores_the_office_even_if_it_is_closer(db_session, clayton_branch):
    from routers.webhooks import _nearest_branch
    clayton_branch.latitude, clayton_branch.longitude = 9.0200, -79.5500
    office = _office(db_session, latitude=9.0000, longitude=-79.5300)       # la oficina queda justo donde está el cliente
    db_session.commit()
    nearest, _km = _nearest_branch(db_session, 9.0000, -79.5300, delivery_only=False)
    assert nearest.id == clayton_branch.id and nearest.id != office.id


def test_choosing_the_hidden_branch_from_a_forged_reply_is_ignored(client, clayton_branch, db_session, listas):
    """Aunque alguien mande a mano el id de la oficina como respuesta de la lista, el bot no la toma."""
    from tests.test_bot_nearest_and_fee import _conv
    office = _office(db_session)
    _post_bot_message(client, PHONE, "wamid.V2", text="retiro")
    from tests.test_bot_nearest_and_fee import _post_interactive_list
    _post_interactive_list(client, PHONE, "wamid.V3", f"branch_{office.id}")
    db_session.expire_all()
    assert _conv(db_session).branch_id != office.id
