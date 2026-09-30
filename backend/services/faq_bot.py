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

# La información adicional (alérgenos, parqueo, mascotas, wifi, política de cancelación...) NO
# vive aquí: se edita desde el panel, en Flujo Visual -> tarjeta "Datos para preguntas
# frecuentes (IA)" (nodo `faq_context`, ver migración 053). Lo que no esté escrito ni aquí ni
# ahí, el bot responde que no sabe.
FAQ_CONTEXT_NODE_ID = "faq_context"
FAQ_CONTEXT_DEFAULT = (
    "Escribe aquí, en frases cortas, lo que el bot puede responder por su cuenta. Ejemplos:\n"
    "- Parqueo: ...\n- Wifi: ...\n- Mascotas: ...\n- Alérgenos / opciones veganas: ...\n"
    "- Política de cancelación: ...\n- Reservas: ..."
)

RESTAURANT_CONTEXT = f"""Eres el asistente de WhatsApp de Farmhouse, un restaurante en Panamá con estas sucursales:
{_BRANCH_LINES}

Formas de pedir: Delivery, Retiro en local, o Pedido corporativo/evento (coordinado por el equipo de catering, {CATERING_PHONE_DISPLAY}).
Formas de pago: ACH/Transferencia, Tarjeta, Yappy.
El menú completo se comparte por un enlace del Menú Digital dentro del chat, no lo describas de memoria.
"""

def build_system_prompt(extra_context: str = "") -> str:
    extra = (extra_context or "").strip()
    # El texto de ejemplo de la tarjeta no es información real: se ignora hasta que lo editen.
    if extra == FAQ_CONTEXT_DEFAULT.strip() or extra.startswith("Escribe aquí"):
        extra = ""
    context = RESTAURANT_CONTEXT + (f"\nInformación adicional del restaurante:\n{extra}\n" if extra else "")
    return SYSTEM_PROMPT_TEMPLATE.replace("{CONTEXT}", context)


SYSTEM_PROMPT_TEMPLATE = f"""{{CONTEXT}}
Un cliente te escribió una pregunta por WhatsApp que no coincidió con ninguna opción de botón del bot. Respóndela SOLO si la puedes contestar con la información de arriba.

Reglas estrictas:
- Si la respuesta no está en la información de arriba, o la pregunta es sobre su pedido/pago/algo que requiere revisar datos del cliente, responde EXACTAMENTE: {NO_SE_SENTINEL}
- Nunca inventes datos (precios, platillos, políticas) que no estén arriba.
- Si respondes, hazlo en 1-3 frases cortas, en español de Panamá, tono cálido y directo, sin encabezados ni markdown (es un mensaje de WhatsApp).
- No repitas el menú de opciones del bot ni pidas que elija un botón."""

SYSTEM_PROMPT = build_system_prompt()


async def answer_faq(question: str, db=None) -> Optional[str]:
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

    extra = ""
    if db is not None:
        try:
            from services.flow_content import get_node_text
            extra = get_node_text(db, FAQ_CONTEXT_NODE_ID, "")
        except Exception:
            extra = ""
    system_prompt = build_system_prompt(extra)

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
                    "system": system_prompt,
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
