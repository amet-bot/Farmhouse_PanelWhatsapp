from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from database import Base


class BotFlow(Base):
    """
    Un flujo de conversación editable desde el panel (pestaña "Flujo visual").

    Guarda el diagrama completo (nodos + conexiones) como JSON en `graph_json`. Es
    deliberadamente una tabla de una sola fila por `key` (no versiones ni borradores en esta
    primera versión): guardar reemplaza el diagrama anterior por completo.

    IMPORTANTE: esta tabla hoy es solo de LECTURA/ESCRITURA desde el panel. El bot real
    (routers/webhooks.py) todavía NO la lee — sigue usando la máquina de estados escrita a
    mano en services/auto_responses.py + _process_auto_flow_background. Conectar el motor
    real del bot a este diagrama es un paso aparte, deliberadamente no incluido aquí (la
    lógica real es mucho más compleja: sucursales dinámicas, interrupciones universales,
    sub-flujo corporativo con retroceso — ver el comentario en la migración 016).
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
