from services.whatsapp_service import MockWhatsAppService
from config import settings
from conftest import TestingSessionLocal, auth_headers_for
from models.contact import Contact
from models.conversation import Conversation


def _incoming_payload(phone_number_id="new-phone-id-987"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "50763644572",
                        "phone_number_id": phone_number_id,
                    },
                    "contacts": [{
                        "profile": {"name": "Cliente Número Nuevo"},
                        "wa_id": "50765523134",
                    }],
                    "messages": [{
                        "from": "50765523134",
                        "id": "wamid.NEWNUMBER001",
                        "timestamp": "1789066400",
                        "text": {"body": "Hola"},
                        "type": "text",
                    }],
                },
                "field": "messages",
            }],
        }],
    }


def test_parser_extracts_receiving_phone_number_id():
    parsed = MockWhatsAppService().parse_incoming_message(_incoming_payload())
    assert parsed["recipient_phone_number_id"] == "new-phone-id-987"
    assert parsed["recipient_display_phone_number"] == "50763644572"


def test_webhook_persists_and_uses_receiving_phone_number_id(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    selected_phone_ids = []
    mock_service = MockWhatsAppService()

    def service_factory(phone_number_id=None):
        selected_phone_ids.append(phone_number_id)
        return mock_service

    monkeypatch.setattr("routers.webhooks.get_whatsapp_service", service_factory)

    response = client.post("/api/webhooks/whatsapp", json=_incoming_payload())
    assert response.status_code == 200, response.text

    conversation = db_session.query(Conversation).filter(
        Conversation.id == response.json()["conversation_id"]
    ).one()
    assert conversation.whatsapp_phone_number_id == "new-phone-id-987"
    # La primera instancia analiza el webhook; la tarea del bot debe crear otra ligada al
    # Phone Number ID que realmente recibió el mensaje.
    assert "new-phone-id-987" in selected_phone_ids


def test_panel_reply_uses_phone_number_id_stored_on_conversation(
    client, db_session, clayton_branch, clayton_agent, clayton_device, monkeypatch
):
    contact = Contact(name="Cliente", phone="+50765523134")
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(
        customer_id=contact.id,
        branch_id=clayton_branch.id,
        status="open",
        whatsapp_phone_number_id="new-phone-id-987",
    )
    db_session.add(conversation)
    db_session.commit()

    selected_phone_ids = []

    class TextService:
        async def send_text_message(self, to_phone, text):
            assert to_phone == "+50765523134"
            assert text == "Respuesta desde el panel"
            return {"messages": [{"id": "wamid.PANELREPLY001"}]}

    def service_factory(phone_number_id=None):
        selected_phone_ids.append(phone_number_id)
        return TextService()

    monkeypatch.setattr("routers.messages.get_whatsapp_service", service_factory)
    response = client.post(
        "/api/messages/",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
        json={
            "conversation_id": conversation.id,
            "content": "Respuesta desde el panel",
            "is_internal": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert selected_phone_ids == ["new-phone-id-987"]
