import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import httpx
import uuid
from config import settings

logger = logging.getLogger("farmhouse.whatsapp")

class WhatsAppService(ABC):
    @abstractmethod
    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        """Envía un mensaje de texto saliente por WhatsApp"""
        pass

    @abstractmethod
    async def send_template_message(self, to_phone: str, template_name: str, language_code: str = "es", components: Optional[list] = None) -> Dict[str, Any]:
        """Envía una plantilla oficial de Meta WhatsApp"""
        pass

    @abstractmethod
    def parse_incoming_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extrae el remitente, texto y wamid de un webhook de Meta"""
        pass

    @abstractmethod
    async def download_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        """Descarga un archivo multimedia de WhatsApp (imagen, video, audio, documento).
        Retorna {"bytes": <bytes>, "mime_type": <str>} o None si no fue posible."""
        pass

    @abstractmethod
    async def send_interactive_list(self, to_phone: str, body_text: str, button_text: str, rows: list) -> Dict[str, Any]:
        """Envía un mensaje de lista interactiva de WhatsApp (menú con botones/opciones)."""
        pass

    @abstractmethod
    async def send_interactive_buttons(self, to_phone: str, body_text: str, buttons: list) -> Dict[str, Any]:
        """Envía un mensaje con botones de respuesta rápida interactivos (máx. 3)."""
        pass

class MockWhatsAppService(WhatsAppService):
    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Mensaje enviado a {to_phone}: {text[:50]}... (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "contacts": [{"input": to_phone, "wa_id": to_phone}], "messages": [{"id": wamid}]}

    async def send_template_message(self, to_phone: str, template_name: str, language_code: str = "es", components: Optional[list] = None) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Plantilla '{template_name}' enviada a {to_phone} (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def send_interactive_list(self, to_phone: str, body_text: str, button_text: str, rows: list) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Lista interactiva enviada a {to_phone}: '{body_text}' con {len(rows)} opciones (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def send_interactive_buttons(self, to_phone: str, body_text: str, buttons: list) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Botones interactivos enviados a {to_phone}: '{body_text}' con {len(buttons)} botones (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def download_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        logger.info(f"[MockWhatsAppService] Modo prueba: no se descarga archivo real para media_id={media_id}.")
        return None

    def parse_incoming_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])
            if not messages:
                return None
            msg = messages[0]
            contact = contacts[0] if contacts else {}
            msg_type = msg.get("type", "text")

            result = {
                "from_phone": msg.get("from"),
                "contact_name": contact.get("profile", {}).get("name", "Cliente WhatsApp"),
                "wamid": msg.get("id"),
                "timestamp": msg.get("timestamp"),
                "message_type": msg_type,
                "text": "",
                "media_id": None,
                "media_mime_type": None,
                "caption": None,
                "interactive_id": None,
                "interactive_title": None,
            }

            if msg_type == "text":
                result["text"] = msg.get("text", {}).get("body", "")
            elif msg_type in ("image", "video", "audio", "document", "sticker"):
                media_obj = msg.get(msg_type, {})
                result["media_id"] = media_obj.get("id")
                result["media_mime_type"] = media_obj.get("mime_type")
                result["caption"] = media_obj.get("caption")
            elif msg_type == "interactive":
                interactive_obj = msg.get("interactive", {})
                interactive_type = interactive_obj.get("type")  # "list_reply" o "button_reply"
                reply_obj = interactive_obj.get(interactive_type, {}) if interactive_type else {}
                result["interactive_id"] = reply_obj.get("id")
                result["interactive_title"] = reply_obj.get("title")
                result["text"] = reply_obj.get("title", "")
            else:
                result["text"] = f"[Mensaje de tipo '{msg_type}' no soportado todavía]"

            return result
        except Exception as e:
            logger.error(f"[MockWhatsAppService] Error parsing incoming webhook payload: {e}")
            return None

    def parse_incoming_status(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extrae actualizaciones de estado (sent, delivered, read, failed) de Meta WhatsApp Webhook."""
        try:
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0]
            value = change.get("value", {})
            statuses = value.get("statuses", [])
            if not statuses:
                return None
            status_obj = statuses[0]
            errors = status_obj.get("errors", [])
            error_msg = errors[0].get("title") or errors[0].get("message") if errors else None
            return {
                "wamid": status_obj.get("id"),
                "status": status_obj.get("status"), # "sent", "delivered", "read", "failed"
                "timestamp": status_obj.get("timestamp"),
                "recipient_id": status_obj.get("recipient_id"),
                "error": error_msg
            }
        except Exception as e:
            logger.error(f"[WhatsAppService] Error parsing incoming status update: {e}")
            return None


class MetaWhatsAppService(WhatsAppService):
    def __init__(self):
        self.api_url = settings.META_WA_API_URL.strip() if settings.META_WA_API_URL else "https://graph.facebook.com/v20.0"
        self.phone_number_id = str(settings.META_WA_PHONE_NUMBER_ID or "").strip()
        raw_token = str(settings.META_WA_ACCESS_TOKEN or "").strip()

        # Validación estricta de caracteres ASCII para evitar fallos de codificación HTTP
        if not raw_token:
            raise ValueError("[MetaWhatsAppService] META_WA_ACCESS_TOKEN está vacío.")
        if not raw_token.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in raw_token):
            logger.error("[MetaWhatsAppService] ERROR CRÍTICO: META_WA_ACCESS_TOKEN contiene caracteres no-ASCII corruptos.")
            raise ValueError("[MetaWhatsAppService] META_WA_ACCESS_TOKEN contiene caracteres no-ASCII o corruptos. Vuelve a copiar el token desde Meta for Developers.")
        if raw_token.startswith("<") or raw_token.endswith(">") or "PEGO_AQUI" in raw_token or "TOKEN_REAL" in raw_token:
            logger.error("[MetaWhatsAppService] ERROR: META_WA_ACCESS_TOKEN contiene un texto placeholder en lugar de un token real.")
            raise ValueError("[MetaWhatsAppService] META_WA_ACCESS_TOKEN contiene un texto placeholder. Pega tu token real de Meta en backend/.env.")

        self.access_token = raw_token

    async def send_text_message(self, to_phone: str, text: str) -> Dict[str, Any]:
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        to_phone_clean = "".join(c for c in str(to_phone) if c.isdigit())
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone_clean,
            "type": "text",
            "text": {"preview_url": False, "body": text}
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta WhatsApp API al enviar a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_template_message(self, to_phone: str, template_name: str, language_code: str = "es", components: Optional[list] = None) -> Dict[str, Any]:
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        to_phone_clean = "".join(c for c in str(to_phone) if c.isdigit())
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "to": to_phone_clean,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components or []
            }
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta WhatsApp API al enviar plantilla a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_interactive_list(self, to_phone: str, body_text: str, button_text: str, rows: list) -> Dict[str, Any]:
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        to_phone_clean = "".join(c for c in str(to_phone) if c.isdigit())
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone_clean,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text},
                "action": {
                    "button": button_text,
                    "sections": [
                        {"title": "Sucursales Farmhouse", "rows": rows}
                    ]
                }
            }
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta al enviar lista interactiva a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_interactive_buttons(self, to_phone: str, body_text: str, buttons: list) -> Dict[str, Any]:
        to_phone_clean = "".join(filter(str.isdigit, to_phone))
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "to": to_phone_clean,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                        for b in buttons
                    ]
                }
            }
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta al enviar botones interactivos a '{to_phone_clean}': {e.response.text}")
                raise e

    async def download_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient() as client:
            try:
                # Paso 1: Meta nos da una URL temporal (caduca en minutos) + el mime_type real
                meta_resp = await client.get(f"{self.api_url}/{media_id}", headers=headers, timeout=10.0)
                meta_resp.raise_for_status()
                media_info = meta_resp.json()
                media_url = media_info.get("url")
                mime_type = media_info.get("mime_type", "application/octet-stream")
                if not media_url:
                    logger.error(f"[MetaWhatsAppService] Meta no devolvió URL para media_id={media_id}: {media_info}")
                    return None

                # Paso 2: descargar el archivo real desde esa URL (también requiere el token)
                file_resp = await client.get(media_url, headers=headers, timeout=20.0)
                file_resp.raise_for_status()
                return {"bytes": file_resp.content, "mime_type": mime_type}
            except Exception as e:
                logger.error(f"[MetaWhatsAppService] Error descargando media_id={media_id}: {e}", exc_info=True)
                return None

    def parse_incoming_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mock_parser = MockWhatsAppService()
        return mock_parser.parse_incoming_message(payload)

    def parse_incoming_status(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mock_parser = MockWhatsAppService()
        return mock_parser.parse_incoming_status(payload)


def get_whatsapp_service() -> WhatsAppService:
    token = str(settings.META_WA_ACCESS_TOKEN or "").strip()
    phone_id = str(settings.META_WA_PHONE_NUMBER_ID or "").strip()
    if settings.WHATSAPP_MODE == "meta" and token and phone_id:
        try:
            return MetaWhatsAppService()
        except Exception as e:
            logger.error(f"[get_whatsapp_service] Error inicializando MetaWhatsAppService: {e}. Usando MockWhatsAppService como respaldo temporal.")
            return MockWhatsAppService()
    return MockWhatsAppService()
