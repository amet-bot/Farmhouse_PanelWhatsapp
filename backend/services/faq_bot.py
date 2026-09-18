"""
Respaldo de preguntas frecuentes con IA (Claude), Bloque 9.5 de `_process_auto_flow_background`
en routers/webhooks.py.

Alcance deliberadamente acotado, mismo criterio que services/flow_engine.py: esto NO reemplaza
ni interrumpe el flujo guiado (pedidos, pagos, corporativo, navegación) — solo se consulta como
último recurso, cuando un mensaje de texto libre no coincidió con ningún botón/intención
conocida y va a caer en `_step_recover_conversation_context`. Si esta función no está segura de
la respuesta, o algo falla (red, API caída, sin API key), devuelve None y la conversación sigue
exactamente igual que antes de que este módulo existiera.

Para editar qué sabe el bot (horarios, políticas, info general), edita RESTAURANT_CONTEXT más
abajo — no hace falta tocar el resto del archivo.
"""
import logging
from typing import Optional

import httpx

from config import settings
from services.auto_responses import BRANCH_VISIT_INFO, CATERING_PHONE_DISPLAY

logger = logging.getLogger("farmhouse.faq_bot")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# Sentinela que le pedimos al modelo devolver textualmente cuando la pregunta no se puede
# responder con la información de abajo (nunca debe inventar datos de Farmhouse).
NO_SE_SENTINEL = "NO_SE"

_BRANCH_LINES = "\n".join(
    f"- {info['name']}: {info['address']}. Horario: {info['hours']}."
    for info in BRANCH_VISIT_INFO.values()
)

# TODO(Sol/equipo Farmhouse): agregar aquí info adicional que el bot deba conocer para
# preguntas frecuentes (platillos con alérgenos, política de cancelación, si aceptan mascotas,
# wifi, parqueo, etc.). Todo lo que NO esté escrito aquí, el bot debe responder que no sabe.
RESTAURANT_CONTEXT = f"""Eres el asistente de WhatsApp de Farmhouse, un restaurante en Panamá con estas sucursales:
{_BRANCH_LINES}

Formas de pedir: Delivery, Retiro en local, o Pedido corporativo/evento (coordinado por el equipo de catering, {CATERING_PHONE_DISPLAY}).
Formas de pago: ACH/Transferencia, Tarjeta, Yappy.
El menú completo se comparte por un enlace del Menú Digital dentro del chat, no lo describas de memoria.
"""

SYSTEM_PROMPT = f"""{RESTAURANT_CONTEXT}
Un cliente te escribió una pregunta por WhatsApp que no coincidió con ninguna opción de botón del bot. Respóndela SOLO si la puedes contestar con la información de arriba.

Reglas estrictas:
- Si la respuesta no está en la información de arriba, o la pregunta es sobre su pedido/pago/algo que requiere revisar datos del cliente, responde EXACTAMENTE: {NO_SE_SENTINEL}
- Nunca inventes datos (precios, platillos, políticas) que no estén arriba.
- Si respondes, hazlo en 1-3 frases cortas, en español de Panamá, tono cálido y directo, sin encabezados ni markdown (es un mensaje de WhatsApp).
- No repitas el menú de opciones del bot ni pidas que elija un botón."""


async def answer_faq(question: str) -> Optional[str]:
    """
    Intenta responder `question` con la info de RESTAURANT_CONTEXT vía la API de Claude.
    Devuelve el texto de la respuesta, o None si: el respaldo está apagado (FAQ_BOT_ENABLED),
    no hay API key configurada, el modelo no está seguro (sentinela NO_SE_SENTINEL), o la
    llamada falla por cualquier motivo (nunca lanza, nunca traba la conversación).
    """
    if not settings.FAQ_BOT_ENABLED:
        return None
    api_key = str(settings.ANTHROPIC_API_KEY or "").strip()
    if not api_key:
        return None
    question = (question or "").strip()
    if not question:
        return None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": settings.ANTHROPIC_MODEL,
                    "max_tokens": 300,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": question}],
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning("[FaqBot] No se pudo consultar la API de Claude.", exc_info=True)
        return None

    content = payload.get("content") or []
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()

    if not text or NO_SE_SENTINEL in text:
        return None
    return text
