"""
Regla ÚNICA de audiencia para notificaciones: quién tiene derecho a enterarse de una
conversación según su rol y su sucursal.

Por qué existe este módulo: la regla ya estaba implementada en el listado REST de
conversaciones (routers/conversations.py, el filtrado del query), pero NUNCA se replicó en
la capa de notificaciones. El resultado era que un agente no podía *listar* las
conversaciones de otra sucursal, pero sí recibía sus notificaciones en tiempo real y por
Web Push, con el nombre del contacto y el contenido del mensaje dentro del payload.

Tener la regla en un solo lugar es lo que evita que las dos capas se vuelvan a separar.
Cualquier canal nuevo (WebSocket, Web Push, correo, SMS) debe preguntar aquí.
"""

from typing import Optional

# Roles con visibilidad global sin importar la sucursal.
GLOBAL_ROLES = ("admin",)


def is_global_viewer(role: Optional[str], user_branch_id: Optional[int]) -> bool:
    """
    ¿Este usuario ve TODAS las sucursales?

    - admin: siempre.
    - supervisor SIN sucursal asignada (branch_id nulo): es supervisor global, ve todo.
    - supervisor CON sucursal: NO es global, queda limitado a su sucursal.
    - agent: nunca.
    """
    if role in GLOBAL_ROLES:
        return True
    if role == "supervisor" and user_branch_id is None:
        return True
    return False


def can_receive_branch_event(
    role: Optional[str],
    user_branch_id: Optional[int],
    conversation_branch_id: Optional[int],
) -> bool:
    """
    ¿Puede este usuario recibir una notificación de una conversación de
    `conversation_branch_id`?

    `conversation_branch_id` nulo significa que la conversación todavía no tiene sucursal
    asignada (el bot aún no la enrutó). Esas solo se anuncian a quien ve global: un agente
    nunca recibe una conversación sin sucursal, porque tampoco puede abrirla, y un
    supervisor de sucursal tampoco, porque no le corresponde ninguna sucursal todavía.
    """
    if is_global_viewer(role, user_branch_id):
        return True
    # Agentes y supervisores de sucursal: exigen coincidencia exacta.
    if conversation_branch_id is None or user_branch_id is None:
        return False
    return int(user_branch_id) == int(conversation_branch_id)
