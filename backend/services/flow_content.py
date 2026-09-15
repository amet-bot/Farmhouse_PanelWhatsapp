"""
Contenido editable del bot real desde la pestaña "Flujo visual" (Fase 2).

A diferencia de farmhouse-catering-center, aquí la lógica de control (interrupciones
universales, sucursales dinámicas) se queda tal cual en routers/webhooks.py, ya probada en
producción — NO hay un motor genérico que "camina" todo el grafo. La única excepción,
deliberadamente acotada, es el sub-flujo de 4 preguntas de Pedido Corporativo/Evento: ahí sí
hay un motor mínimo (services/flow_engine.py) que sigue las CONEXIONES del grafo para decidir
qué pregunta sigue (ver `_advance_corporate_step` en webhooks.py).

Este módulo (`flow_content`) resuelve una sola cosa: qué TEXTO/ETIQUETAS de botón mostrar para
un paso dado, leyendo el grafo `main_intake` (tabla bot_flows) si el admin lo editó, y cayendo
de vuelta al texto original (services/auto_responses.py) en cualquier otro caso — grafo
ausente, nodo ausente, texto vacío, o (para opciones) una cantidad de botones que ya no
coincide con lo que el código espera. Ese respaldo automático es lo que hace seguro este
cambio: un diagrama roto o a medio editar nunca puede tumbar ni desviar una conversación real.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models.bot_flow import BotFlow

logger = logging.getLogger("farmhouse.flow_content")

MAIN_FLOW_KEY = "main_intake"


def _load_graph(db: Optional[Session]) -> Optional[Dict[str, Any]]:
    if db is None:
        # Permite que las funciones de auto_responses.py sigan siendo llamables sin `db` (ej.
        # el cálculo de constantes a nivel de módulo en import time, o en tests que no
        # necesitan la variante editable) — sin `db` simplemente no hay nada que consultar.
        return None
    try:
        flow = db.query(BotFlow).filter(BotFlow.key == MAIN_FLOW_KEY).first()
        if not flow:
            return None
        return json.loads(flow.graph_json)
    except Exception:
        logger.warning("[FlowContent] No se pudo cargar/parsear el grafo '%s'.", MAIN_FLOW_KEY, exc_info=True)
        return None


def _node_by_id(graph: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    for n in graph.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def get_graph(db: Optional[Session]) -> Optional[Dict[str, Any]]:
    """Punto de entrada público para módulos hermanos (ej. services/flow_engine.py) que
    necesitan el grafo completo (nodos + conexiones), no solo el texto de un nodo."""
    return _load_graph(db)


def find_node(graph: Optional[Dict[str, Any]], node_id: str) -> Optional[Dict[str, Any]]:
    """Igual que `get_node_text`/`get_node_options` internamente, pero expuesto para
    módulos hermanos que ya tienen el grafo cargado (evita recargarlo/reparsear el JSON)."""
    if not graph:
        return None
    return _node_by_id(graph, node_id)


def get_node_text(db: Optional[Session], node_id: str, fallback: str, **template_vars: Any) -> str:
    """Texto editable de un nodo tipo 'message'/'question' (el mensaje/pregunta en sí, no las
    etiquetas de sus botones). Si el nodo no existe o su texto quedó vacío, usa `fallback` tal
    cual (ya viene formateado por el caller con sus propios valores dinámicos)."""
    graph = _load_graph(db)
    node = _node_by_id(graph, node_id) if graph else None
    text = (node.get("text") if node else None) or ""
    if not text.strip():
        return fallback
    for key, value in template_vars.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def get_node_options(db: Optional[Session], node_id: str, fallback_titles: List[str]) -> List[str]:
    """Etiquetas visibles de los botones de un nodo tipo 'question'. El ID/valor real de cada
    botón (ej. "order_delivery") NUNCA cambia — solo se sustituye el título en la misma
    posición. Si el nodo no existe, no es de tipo 'question', o su cantidad de opciones no
    coincide EXACTAMENTE con `len(fallback_titles)`, se ignora por completo y se devuelven las
    etiquetas por defecto (evita que agregar/quitar una opción en el editor rompa el mapeo
    posición->id que sigue viviendo en Python)."""
    graph = _load_graph(db)
    node = _node_by_id(graph, node_id) if graph else None
    if not node or node.get("type") != "question":
        return fallback_titles
    options = node.get("options")
    if not isinstance(options, list) or len(options) != len(fallback_titles):
        return fallback_titles
    cleaned = [str(o).strip() for o in options]
    if any(not o for o in cleaned):
        return fallback_titles
    return cleaned
