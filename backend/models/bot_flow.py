from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from database import Base


class BotFlow(Base):
    """
    Un flujo de conversación editable desde el panel (pestaña "Flujo visual").

    Guarda el diagrama completo (nodos + conexiones) como JSON en `graph_json`. Es
    deliberadamente una tabla de una sola fila por `key` (no versiones ni borradores en esta
    primera versión): guardar reemplaza el diagrama anterior por completo.

    El bot real (routers/webhooks.py) SÍ lee este grafo en dos niveles:
    1. Contenido (todos los nodos): el texto/opciones de cada paso se puede editar aquí y el
       bot los usa tal cual (ver services/flow_content.py), con respaldo automático al texto
       original si el nodo se rompe.
    2. Secuencia (solo el sub-flujo de Pedido Corporativo/Evento, deliberadamente acotado):
       las CONEXIONES entre esas 4 preguntas también se siguen de verdad (ver
       services/flow_engine.py + `_advance_corporate_step` en webhooks.py), así que
       reordenarlas ahí sí cambia qué pregunta sigue a cuál.
    El resto de la lógica de control (sucursales dinámicas, interrupciones universales, pagos,
    ayuda de gerente) sigue siendo la máquina de estados escrita a mano en
    services/auto_responses.py + _process_auto_flow_background — convertir TODO el bot en un
    grafo genérico requeriría un lenguaje de nodos tan complejo como el código mismo, con
    demasiado riesgo para un bot que ya atiende clientes reales (decisión tomada con el
    usuario el 2026-09-14).
    """
    __tablename__ = "bot_flows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Identificador estable del flujo, ej. "main_intake". Único: por ahora este panel solo
    # administra un flujo, pero el modelo ya soporta agregar más sin migrar de nuevo.
    key = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    graph_json = Column(Text, nullable=False)
    updated_at = Column(
        DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
