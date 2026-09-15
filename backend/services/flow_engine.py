"""
Motor mínimo que hace que las CONEXIONES del grafo `main_intake` (tabla bot_flows, pestaña
"Flujo visual") tengan efecto real en el bot — no solo el texto de cada nodo (eso ya lo hace
services/flow_content.py).

Alcance deliberadamente acotado, acordado con el usuario el 2026-09-14 tras revisar que
`routers/webhooks.py::_process_auto_flow_background` es una máquina de estados con detección
de lenguaje natural en varios puntos, recuperación de contexto e interrupciones universales —
convertir TODO eso en un grafo genérico requeriría un lenguaje de nodos tan complejo como el
código mismo, con alto riesgo para un bot que ya atiende clientes reales. En vez de eso, este
motor SOLO decide "cuál es el siguiente nodo" dentro de puntos de avance ya lineales y
acotados (hoy: el sub-flujo de 4 preguntas de Pedido Corporativo/Evento, ver
`_advance_corporate_step` en webhooks.py) — la detección de intención, validación de
respuesta y todo lo demás se queda exactamente igual en Python.

Filosofía de seguridad (igual que flow_content.py): si el grafo no existe, no tiene esa
conexión, o el nodo destino ya no existe (el admin lo borró/renombró), se usa el nodo de
respaldo indicado por el caller — un diagrama roto o a medio editar nunca puede trabar ni
desviar una conversación real.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from services.flow_content import get_graph, find_node

logger = logging.getLogger("farmhouse.flow_engine")


def get_next_node_id(
    db: Optional[Session],
    from_node_id: str,
    port: int,
    fallback_node_id: Optional[str],
) -> Optional[str]:
    """
    Nodo destino de la conexión (`from_node_id`, `port`) en el grafo `main_intake`, según lo
    haya dejado el admin en el editor visual. Si hay varias conexiones para el mismo
    (`from_node_id`, `port`) se usa la primera (no debería ocurrir con el editor actual, que
    solo permite una conexión por puerto).

    Devuelve `fallback_node_id` si: no hay grafo guardado, no existe esa conexión, o el nodo
    destino ya no está en la lista de nodos del grafo.
    """
    graph = get_graph(db)
    if not graph:
        return fallback_node_id

    for conn in graph.get("conns", []):
        if conn.get("from") == from_node_id and conn.get("fromPort", 0) == port:
            target = conn.get("to")
            if target and find_node(graph, target):
                return target
            logger.warning(
                "[FlowEngine] Conexión '%s' puerto %s apunta a un nodo inexistente ('%s'); usando respaldo '%s'.",
                from_node_id, port, target, fallback_node_id,
            )
            break

    return fallback_node_id
