import pytest
from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation

def test_agent_cannot_see_or_take_other_branch_conversations(
    client, db_session, clayton_branch, obarrio_branch, clayton_agent, obarrio_agent, clayton_device
):
    """
    Requisito (a): Un agente no puede ver ni tomar conversaciones de otra sucursal.
    """
    # 1. Crear contacto y conversación asignada a Obarrio
    contact = Contact(name="Cliente Obarrio", phone="+50761112233")
    db_session.add(contact)
    db_session.commit()

    conv_obarrio = Conversation(
        customer_id=contact.id,
        branch_id=obarrio_branch.id,
        status="unassigned"
    )
    db_session.add(conv_obarrio)
    db_session.commit()

    # 2. Agente de Clayton intenta listar conversaciones
    headers_clayton = auth_headers_for(clayton_agent, clayton_device.device_id)
    res_list = client.get("/api/conversations/", headers=headers_clayton)
    assert res_list.status_code == 200
    convs = res_list.json()
    # La conversación de Obarrio NO debe aparecer en el listado del agente de Clayton
    assert all(c["id"] != conv_obarrio.id for c in convs)

    # 3. Agente de Clayton intenta ver el detalle de la conversación de Obarrio -> Debe dar 403
    res_detail = client.get(f"/api/conversations/{conv_obarrio.id}", headers=headers_clayton)
    assert res_detail.status_code == 403
    assert "No tienes acceso" in res_detail.json()["detail"]

    # 4. Agente de Clayton intenta tomar la conversación de Obarrio -> Debe dar 403
    res_take = client.post(f"/api/conversations/{conv_obarrio.id}/take", headers=headers_clayton)
    assert res_take.status_code == 403
