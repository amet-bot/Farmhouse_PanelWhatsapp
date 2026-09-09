import asyncio
from datetime import datetime, timezone

from conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.whatsapp_service import MetaWhatsAppService


def _make_conversation(db_session, branch):
    contact = Contact(
        name="Cliente Archivos",
        phone="+50760001111",
        created_at=datetime.now(timezone.utc),
        last_interaction=datetime.now(timezone.utc),
    )
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(
        customer_id=contact.id,
        branch_id=branch.id,
        status="open",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation


class RecordingMediaService:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def send_media_message(self, to_phone, media_bytes, mime_type, media_type, filename=None, caption=None):
        self.calls.append({
            "to_phone": to_phone,
            "media_bytes": media_bytes,
            "mime_type": mime_type,
            "media_type": media_type,
            "filename": filename,
            "caption": caption,
        })
        if self.fail:
            raise RuntimeError("Meta temporalmente no disponible")
        return {
            "messages": [{"id": "wamid.OUTGOINGMEDIA"}],
            "uploaded_media_id": "meta-media-123",
        }

    async def send_text_message(self, *_args, **_kwargs):
        raise AssertionError("Un reintento de archivo no debe enviarse como texto")


def test_agent_can_send_image_with_caption(
    client, clayton_branch, clayton_agent, clayton_device, db_session, monkeypatch
):
    conversation = _make_conversation(db_session, clayton_branch)
    service = RecordingMediaService()
    monkeypatch.setattr("routers.messages.get_whatsapp_service", lambda: service)

    response = client.post(
        "/api/messages/media",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
        data={"conversation_id": str(conversation.id), "caption": "Aquí está tu pedido 😊"},
        files={"file": ("pedido.png", b"fake-png-content", "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["media_type"] == "image"
    assert body["media_mime_type"] == "image/png"
    assert body["media_url"].startswith("/media/incoming/")
    assert body["content"] == "Aquí está tu pedido 😊"
    assert service.calls[0]["filename"] == "pedido.png"
    assert service.calls[0]["caption"] == "Aquí está tu pedido 😊"


def test_agent_can_send_word_document(
    client, clayton_branch, clayton_agent, clayton_device, db_session, monkeypatch
):
    conversation = _make_conversation(db_session, clayton_branch)
    service = RecordingMediaService()
    monkeypatch.setattr("routers.messages.get_whatsapp_service", lambda: service)

    response = client.post(
        "/api/messages/media",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
        data={"conversation_id": str(conversation.id), "caption": "Cotización"},
        files={
            "file": (
                "cotizacion.docx",
                b"fake-docx-content",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["media_type"] == "document"
    assert response.json()["content"] == "cotizacion.docx"
    assert service.calls[0]["caption"] == "Cotización"


def test_outgoing_media_rejects_unsupported_file(
    client, clayton_branch, clayton_agent, clayton_device, db_session
):
    conversation = _make_conversation(db_session, clayton_branch)
    response = client.post(
        "/api/messages/media",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
        data={"conversation_id": str(conversation.id)},
        files={"file": ("programa.exe", b"not-allowed", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_failed_image_retry_sends_the_file_again(
    client, clayton_branch, clayton_agent, clayton_device, db_session, monkeypatch
):
    conversation = _make_conversation(db_session, clayton_branch)
    failing_service = RecordingMediaService(fail=True)
    monkeypatch.setattr("routers.messages.get_whatsapp_service", lambda: failing_service)

    first_response = client.post(
        "/api/messages/media",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
        data={"conversation_id": str(conversation.id), "caption": "Foto de prueba"},
        files={"file": ("foto.jpg", b"fake-jpeg-content", "image/jpeg")},
    )
    assert first_response.status_code == 200
    assert first_response.json()["status"] == "failed"

    message = db_session.query(Message).filter(Message.id == first_response.json()["id"]).one()
    retry_service = RecordingMediaService()
    monkeypatch.setattr("routers.messages.get_whatsapp_service", lambda: retry_service)
    retry_response = client.post(
        f"/api/messages/{message.id}/retry",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
    )

    assert retry_response.status_code == 200, retry_response.text
    assert retry_response.json()["status"] == "sent"
    assert retry_service.calls[0]["media_bytes"] == b"fake-jpeg-content"
    assert retry_service.calls[0]["media_type"] == "image"


def test_meta_service_uploads_media_before_sending_message(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/media"):
                return FakeResponse({"id": "uploaded-456"})
            return FakeResponse({"messages": [{"id": "wamid.SENT456"}]})

    monkeypatch.setattr("services.whatsapp_service.httpx.AsyncClient", FakeAsyncClient)
    service = object.__new__(MetaWhatsAppService)
    service.api_url = "https://graph.facebook.com/v20.0"
    service.phone_number_id = "phone-id"
    service.access_token = "secret-token"

    result = asyncio.run(service.send_media_message(
        "50760001111",
        b"pdf-bytes",
        "application/pdf",
        "document",
        filename="cotizacion.pdf",
        caption="Documento solicitado",
    ))

    assert len(calls) == 2
    assert calls[0][0].endswith("/phone-id/media")
    assert calls[0][1]["data"] == {"messaging_product": "whatsapp"}
    assert calls[0][1]["files"]["file"] == ("cotizacion.pdf", b"pdf-bytes", "application/pdf")
    assert calls[1][0].endswith("/phone-id/messages")
    assert calls[1][1]["json"]["document"] == {
        "id": "uploaded-456",
        "caption": "Documento solicitado",
        "filename": "cotizacion.pdf",
    }
    assert result["uploaded_media_id"] == "uploaded-456"
