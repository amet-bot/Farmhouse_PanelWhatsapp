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
    async def send_media_message(
        self,
        to_phone: str,
        media_bytes: bytes,
        mime_type: str,
        media_type: str,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sube una imagen o documento a Meta y lo envía al contacto."""
        pass

    @abstractmethod
    async def send_interactive_list(self, to_phone: str, body_text: str, button_text: str, rows: list) -> Dict[str, Any]:
        """Envía un mensaje de lista interactiva de WhatsApp (menú con botones/opciones)."""
        pass

    @abstractmethod
    async def send_interactive_buttons(self, to_phone: str, body_text: str, buttons: list) -> Dict[str, Any]:
        """Envía un mensaje con botones de respuesta rápida interactivos (máx. 3)."""
        pass

    @abstractmethod
    async def send_catalog_message(self, to_phone: str, body_text: str, catalog_id: Optional[str] = None) -> Dict[str, Any]:
        """Envía un mensaje con CTA que abre el catálogo completo de Meta Commerce conectado al WABA."""
        pass

    @abstractmethod
    async def send_product_list_message(self, to_phone: str, header_text: str, body_text: str, catalog_id: str, sections: list, footer_text: Optional[str] = None) -> Dict[str, Any]:
        """Envía un mensaje de lista de productos (Multi-Product Message) desde el catálogo de Meta."""
        pass

    @abstractmethod
    async def send_cta_url_message(self, to_phone: str, body_text: str, button_text: str, url: str) -> Dict[str, Any]:
        """Envía un mensaje interactivo con un botón que abre una URL externa (CTA URL button),
        en vez de mostrar el link como texto plano dentro del mensaje."""
        pass

    @abstractmethod
    async def send_typing_indicator(self, wamid: str) -> None:
        """Marca el mensaje entrante como leído y muestra el indicador "escribiendo..." en el
        chat del cliente (dura hasta 25s o hasta que llegue el próximo mensaje nuestro, lo que
        pase primero). Es un efecto secundario puramente cosmético: nunca debe interrumpir el
        flujo de respuesta si falla, por eso no propaga excepciones."""
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

    async def send_media_message(
        self,
        to_phone: str,
        media_bytes: bytes,
        mime_type: str,
        media_type: str,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        media_id = f"mock-media-{uuid.uuid4().hex}"
        logger.info(
            f"[MockWhatsAppService] {media_type} '{filename or 'archivo'}' enviado a "
            f"{to_phone} ({len(media_bytes)} bytes, WAMID: {wamid})"
        )
        return {
            "messaging_product": "whatsapp",
            "messages": [{"id": wamid}],
            "uploaded_media_id": media_id,
        }

    async def send_catalog_message(self, to_phone: str, body_text: str, catalog_id: Optional[str] = None) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Mensaje de catálogo enviado a {to_phone}: '{body_text}' (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def send_product_list_message(self, to_phone: str, header_text: str, body_text: str, catalog_id: str, sections: list, footer_text: Optional[str] = None) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        total_items = sum(len(s.get("product_items", [])) for s in sections)
        logger.info(f"[MockWhatsAppService] Lista de productos enviada a {to_phone}: {len(sections)} secciones, {total_items} productos (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def send_cta_url_message(self, to_phone: str, body_text: str, button_text: str, url: str) -> Dict[str, Any]:
        wamid = f"wamid.HBgL{uuid.uuid4().hex[:16].upper()}"
        logger.info(f"[MockWhatsAppService] Botón CTA '{button_text}' -> {url} enviado a {to_phone} (WAMID: {wamid})")
        return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}

    async def send_typing_indicator(self, wamid: str) -> None:
        logger.info(f"[MockWhatsAppService] Simulando 'escribiendo...' para wamid={wamid}")

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
                reply_obj = interactive_obj.get(interactive_type, {}) if interactive_type else (
                    interactive_obj.get("list_reply") or interactive_obj.get("button_reply") or {}
                )
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

    async def send_media_message(
        self,
        to_phone: str,
        media_bytes: bytes,
        mime_type: str,
        media_type: str,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Carga el archivo a WhatsApp Cloud API y envía el ID resultante en un mensaje."""
        if media_type not in ("image", "document"):
            raise ValueError(f"Tipo de archivo saliente no soportado: {media_type}")

        to_phone_clean = "".join(c for c in str(to_phone) if c.isdigit())
        safe_filename = (filename or "archivo").replace("\r", "").replace("\n", "")[:240]
        auth_headers = {"Authorization": f"Bearer {self.access_token}"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                upload_response = await client.post(
                    f"{self.api_url}/{self.phone_number_id}/media",
                    headers=auth_headers,
                    data={"messaging_product": "whatsapp"},
                    files={"file": (safe_filename, media_bytes, mime_type)},
                )
                upload_response.raise_for_status()
                uploaded_media_id = upload_response.json().get("id")
                if not uploaded_media_id:
                    raise ValueError("Meta no devolvió el identificador del archivo subido.")

                media_payload: Dict[str, Any] = {"id": uploaded_media_id}
                if caption:
                    media_payload["caption"] = caption[:1024]
                if media_type == "document":
                    media_payload["filename"] = safe_filename

                message_response = await client.post(
                    f"{self.api_url}/{self.phone_number_id}/messages",
                    headers={**auth_headers, "Content-Type": "application/json"},
                    json={
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": to_phone_clean,
                        "type": media_type,
                        media_type: media_payload,
                    },
                )
                message_response.raise_for_status()
                result = message_response.json()
                result["uploaded_media_id"] = uploaded_media_id
                return result
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"[MetaWhatsAppService] Error HTTP {e.response.status_code} enviando "
                    f"{media_type} a '{to_phone_clean}': {e.response.text}"
                )
                raise

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

    async def send_catalog_message(self, to_phone: str, body_text: str, catalog_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Mensaje con botón "Ver catálogo": abre el catálogo completo de Meta Commerce
        Manager conectado a este WABA. El Cloud API no acepta un catalog_id por mensaje
        para este tipo de interactivo (siempre usa el catálogo conectado a la cuenta);
        el parámetro se conserva por consistencia de firma y uso futuro.
        """
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
                "type": "catalog_message",
                "body": {"text": body_text},
                "action": {
                    "name": "catalog_message",
                    "parameters": {}
                }
            }
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta al enviar mensaje de catálogo a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_product_list_message(self, to_phone: str, header_text: str, body_text: str, catalog_id: str, sections: list, footer_text: Optional[str] = None) -> Dict[str, Any]:
        """
        Mensaje de lista de productos (Multi-Product Message): muestra varios productos
        del catálogo agrupados en secciones. `sections` = [{"title": str, "product_items":
        [{"product_retailer_id": sku}, ...]}, ...] (máx. 30 productos / 10 secciones, límites de Meta).
        """
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        to_phone_clean = "".join(c for c in str(to_phone) if c.isdigit())
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        interactive: Dict[str, Any] = {
            "type": "product_list",
            "header": {"type": "text", "text": header_text},
            "body": {"text": body_text},
            "action": {
                "catalog_id": catalog_id,
                "sections": sections
            }
        }
        if footer_text:
            interactive["footer"] = {"text": footer_text}
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone_clean,
            "type": "interactive",
            "interactive": interactive
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta al enviar lista de productos a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_cta_url_message(self, to_phone: str, body_text: str, button_text: str, url: str) -> Dict[str, Any]:
        url_endpoint = f"{self.api_url}/{self.phone_number_id}/messages"
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
                "type": "cta_url",
                "body": {"text": body_text},
                "action": {
                    "name": "cta_url",
                    "parameters": {"display_text": button_text[:20], "url": url}
                }
            }
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url_endpoint, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[MetaWhatsAppService] Error HTTP {e.response.status_code} de Meta al enviar botón CTA URL a '{to_phone_clean}': {e.response.text}")
                raise e

    async def send_typing_indicator(self, wamid: str) -> None:
        if not wamid:
            return
        url = f"{self.api_url}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wamid,
            "typing_indicator": {"type": "text"}
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
                response.raise_for_status()
            except Exception as e:
                # Puramente cosmético (Punto de "sentirse humano"): si Meta lo rechaza (wamid ya
                # expiró, límite de la cuenta, etc.) el bot igual debe seguir y responder normal.
                logger.warning(f"[MetaWhatsAppService] No se pudo mostrar 'escribiendo...' para wamid={wamid}: {e}")

    async def download_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        auth_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "curl/7.64.1"
        }
        cdn_headers = {
            "User-Agent": "curl/7.64.1"
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Paso 1: Meta nos da una URL temporal (caduca en minutos) + el mime_type real
                meta_resp = await client.get(f"{self.api_url}/{media_id}", headers=auth_headers)
                meta_resp.raise_for_status()
                media_info = meta_resp.json()
                media_url = media_info.get("url")
                mime_type = media_info.get("mime_type", "application/octet-stream")
                if not media_url:
                    logger.error(f"[MetaWhatsAppService] Meta no devolvió URL para media_id={media_id}: {media_info}")
                    return None

                # Paso 2: Descargar los bytes reales desde la URL temporal.
                # IMPORTANTE CRÍTICO:
                # - lookaside.fbsbx.com requiere 'Authorization: Bearer <token>' y 'User-Agent'.
                # - Al redirigir hacia el CDN (*.fbcdn.net / *.cdninstagram.com), Facebook CDN RECHAZA
                #   la petición con HTTP 400 Bad Request si lleva el encabezado Authorization.
                #   Por ende, en dominios fbcdn.net o cdninstagram.com se deben enviar ÚNICAMENTE cabeceras
                #   sin token (solo User-Agent).
                target_url = media_url
                for redirect_hop in range(5):
                    is_cdn = "fbcdn.net" in target_url or "cdninstagram.com" in target_url
                    headers_to_send = cdn_headers if is_cdn else auth_headers

                    file_resp = await client.get(target_url, headers=headers_to_send, follow_redirects=False)
                    if file_resp.status_code in (301, 302, 303, 307, 308):
                        target_url = file_resp.headers.get("Location")
                        if not target_url:
                            logger.error(f"[MetaWhatsAppService] Redirección sin Location en salto {redirect_hop} para media_id={media_id}")
                            return None
                        continue
                    file_resp.raise_for_status()
                    if file_resp.content:
                        logger.info(f"[MetaWhatsAppService] Media {media_id} descargado exitosamente ({len(file_resp.content)} bytes, {mime_type})")
                        return {"bytes": file_resp.content, "mime_type": mime_type}
                    break
                logger.error(f"[MetaWhatsAppService] Descarga vacía o demasiadas redirecciones para media_id={media_id}")
                return None
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
