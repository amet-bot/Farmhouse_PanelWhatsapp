from datetime import datetime, timezone

from config import settings
from conftest import auth_headers_for, TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.media_storage import MEDIA_DOWNLOAD_FAILED_MARKER


def _image_webhook_payload(media_id="wamid_media_abc123", wamid="wamid.HBgLIMAGE0001", caption=None):
    image_obj = {"id": media_id, "mime_type": "image/jpeg", "sha256": "deadbeef"}
    if caption:
        image_obj["caption"] = caption
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "50769998888",
                        "id": wamid,
                        "timestamp": "1725000000",
                        "type": "image",
                        "image": image_obj,
                    }],
                    "contacts": [{"profile": {"name": "Cliente Foto"}}]
                }
            }]
        }]
    }


def test_incoming_image_fast_path_downloads_and_persists_media(client, clayton_branch, db_session, monkeypatch):
    """Si Meta responde rápido, el mensaje ya nace con media_url (Estrategia A: almacenamiento persistente)."""
    from services.whatsapp_service import MockWhatsAppService
    # backend/.env trae WHATSAPP_MODE=meta configurado para producción: se fuerza "mock" aquí
    # (mismo patrón que test_webhook_idempotency_duplicate_wamid) para no llamar a la Graph API
    # real durante los tests.
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    # La tarea en background usa database.SessionLocal directamente (no pasa por el Depends(get_db)
    # que sí sobrescribe la fixture `client`), así que sin este parche apuntaría al MySQL real de
    # .env en vez de la base SQLite de pruebas.
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)

    async def fake_download_media(self, media_id):
        assert media_id == "wamid_media_abc123"
        return {"bytes": b"\xff\xd8\xff\xe0fake-jpeg-bytes", "mime_type": "image/jpeg"}

    monkeypatch.setattr(MockWhatsAppService, "download_media", fake_download_media)

    resp = client.post("/api/webhooks/whatsapp", json=_image_webhook_payload())
    assert resp.status_code == 200, resp.text

    msg = db_session.query(Message).filter(Message.whatsapp_message_id == "wamid.HBgLIMAGE0001").first()
    assert msg is not None
    assert msg.media_type == "image"
    assert msg.media_id == "wamid_media_abc123"
    assert msg.media_mime_type == "image/jpeg"
    assert msg.media_url and msg.media_url.startswith("/media/incoming/")
    assert msg.error_detail is None


def test_incoming_image_with_caption_keeps_caption_as_content(client, clayton_branch, db_session, monkeypatch):
    from services.whatsapp_service import MockWhatsAppService
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)

    async def fake_download_media(self, media_id):
        return {"bytes": b"fake-bytes", "mime_type": "image/png"}

    monkeypatch.setattr(MockWhatsAppService, "download_media", fake_download_media)

    resp = client.post("/api/webhooks/whatsapp", json=_image_webhook_payload(
        media_id="wamid_media_caption", wamid="wamid.HBgLIMAGE0002", caption="Comprobante de pago"
    ))
    assert resp.status_code == 200, resp.text

    msg = db_session.query(Message).filter(Message.whatsapp_message_id == "wamid.HBgLIMAGE0002").first()
    assert msg.content == "Comprobante de pago"
    assert msg.media_url is not None


def test_incoming_image_download_failure_marks_error_instead_of_hanging_forever(client, clayton_branch, db_session, monkeypatch):
    """
    Sin más monkeypatch que la sesión de BD: MockWhatsAppService.download_media() devuelve None
    (simula que Meta no pudo resolver el archivo). Ni el intento rápido ni el reintento en
    background lo consiguen, así que el mensaje debe quedar marcado con el error explícito —
    nunca en blanco para siempre (el bug original reportado por el usuario).
    """
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    resp = client.post("/api/webhooks/whatsapp", json=_image_webhook_payload(
        media_id="wamid_media_fails", wamid="wamid.HBgLIMAGE0003"
    ))
    assert resp.status_code == 200, resp.text

    msg = db_session.query(Message).filter(Message.whatsapp_message_id == "wamid.HBgLIMAGE0003").first()
    assert msg is not None
    assert msg.media_type == "image"
    assert msg.media_id == "wamid_media_fails"
    assert msg.media_url is None
    assert msg.error_detail == MEDIA_DOWNLOAD_FAILED_MARKER


def _make_incoming_image_message(db_session, branch, media_id="wamid_media_retry", error=MEDIA_DOWNLOAD_FAILED_MARKER):
    contact = Contact(name="Cliente Foto", phone="+50769998888", created_at=datetime.now(timezone.utc), last_interaction=datetime.now(timezone.utc))
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, branch_id=branch.id, status="open", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db_session.add(conv)
    db_session.flush()
    msg = Message(
        conversation_id=conv.id, direction="incoming", sender_type="customer",
        content="📷 Imagen", media_type="image", media_id=media_id, media_url=None,
        error_detail=error, status="delivered", created_at=datetime.now(timezone.utc)
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    db_session.refresh(conv)
    return msg, conv


def test_retry_media_endpoint_success(client, clayton_branch, clayton_agent, clayton_device, db_session, monkeypatch):
    msg, conv = _make_incoming_image_message(db_session, clayton_branch)

    class FakeWaService:
        async def download_media(self, media_id):
            assert media_id == "wamid_media_retry"
            return {"bytes": b"recovered-bytes", "mime_type": "image/jpeg"}

    monkeypatch.setattr("routers.messages.get_whatsapp_service", lambda: FakeWaService())

    resp = client.post(
        f"/api/messages/{msg.id}/retry-media",
        headers=auth_headers_for(clayton_agent, device_id=clayton_device.device_id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["media_url"] and data["media_url"].startswith("/media/incoming/")
    assert data["error_detail"] is None

    db_session.refresh(msg)
    assert msg.media_url is not None
    assert msg.error_detail is None


def test_retry_media_is_idempotent_when_already_available(client, clayton_branch, clayton_agent, clayton_device, db_session, monkeypatch):
    msg, conv = _make_incoming_image_message(db_session, clayton_branch)
    msg.media_url = "/media/incoming/already-there.jpg"
    msg.error_detail = None
    db_session.commit()

    def boom():
        raise AssertionError("No debería llamarse a get_whatsapp_service si el media ya está disponible")

    monkeypatch.setattr("routers.messages.get_whatsapp_service", boom)

    resp = client.post(
        f"/api/messages/{msg.id}/retry-media",
        headers=auth_headers_for(clayton_agent, device_id=clayton_device.device_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["media_url"] == "/media/incoming/already-there.jpg"


def test_retry_media_denies_agent_from_other_branch(client, clayton_branch, obarrio_branch, obarrio_agent, obarrio_device, db_session):
    msg, conv = _make_incoming_image_message(db_session, clayton_branch)

    resp = client.post(
        f"/api/messages/{msg.id}/retry-media",
        headers=auth_headers_for(obarrio_agent, device_id=obarrio_device.device_id),
    )
    assert resp.status_code == 403


def test_retry_media_without_media_id_returns_400(client, clayton_branch, clayton_agent, clayton_device, db_session):
    msg, conv = _make_incoming_image_message(db_session, clayton_branch, media_id=None)

    resp = client.post(
        f"/api/messages/{msg.id}/retry-media",
        headers=auth_headers_for(clayton_agent, device_id=clayton_device.device_id),
    )
    assert resp.status_code == 400
