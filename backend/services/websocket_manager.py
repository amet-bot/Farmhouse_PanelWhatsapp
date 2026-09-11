import json
import logging
from typing import Dict, Iterable, Optional, Set
from fastapi import WebSocket

from services.notification_audience import can_receive_branch_event, is_global_viewer

logger = logging.getLogger("farmhouse.websocket")


class ConnectionManager:
    """
    Registro de conexiones WebSocket activas y punto único de difusión.

    Cada conexión guarda la identidad con la que se autenticó (user_id, rol, sucursal), y
    TODA difusión se filtra contra esa identidad usando la regla de
    services/notification_audience.py. El rol y la sucursal se toman de la base de datos en
    el handshake (routers/websocket.py), nunca de un parámetro que mande el cliente, así que
    una conexión no puede pedir que la metan en la sala de otra sucursal.
    """

    def __init__(self):
        # Mapeo: user_id -> Set[WebSocket]. Un usuario puede tener varias pestañas abiertas.
        self.active_users: Dict[int, Set[WebSocket]] = {}
        # Mapeo: branch_id -> Set[WebSocket]. Solo con sucursal real (nunca None).
        self.branch_rooms: Dict[int, Set[WebSocket]] = {}
        # Identidad autenticada de cada conexión: WebSocket -> {"user_id", "role", "branch_id"}.
        # Es la fuente de verdad del filtrado; sin esto no se puede decidir si una conexión
        # tiene derecho a un evento.
        self.connection_context: Dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, user_id: int, branch_id: int = None, role: str = "agent"):
        # El accept() ya se hace al inicio de websocket_endpoint (routers/websocket.py),
        # antes de validar credenciales, para que un close(code=...) por auth fallida
        # pueda transmitir su código real al cliente.

        # 1. Registrar usuario
        if user_id not in self.active_users:
            self.active_users[user_id] = set()
        self.active_users[user_id].add(websocket)

        # 2. Registrar la identidad con la que se autenticó esta conexión
        self.connection_context[websocket] = {
            "user_id": user_id,
            "role": role,
            "branch_id": branch_id,
        }

        # 3. Registrar en sala de sucursal (solo informativo/diagnóstico: el filtrado real
        #    se hace por connection_context, no por pertenencia a esta sala)
        if branch_id:
            if branch_id not in self.branch_rooms:
                self.branch_rooms[branch_id] = set()
            self.branch_rooms[branch_id].add(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int, branch_id: int = None, role: str = "agent"):
        if user_id in self.active_users and websocket in self.active_users[user_id]:
            self.active_users[user_id].remove(websocket)
            if not self.active_users[user_id]:
                del self.active_users[user_id]

        if branch_id and branch_id in self.branch_rooms and websocket in self.branch_rooms[branch_id]:
            self.branch_rooms[branch_id].remove(websocket)
            if not self.branch_rooms[branch_id]:
                del self.branch_rooms[branch_id]

        self.connection_context.pop(websocket, None)

    # ------------------------------------------------------------------
    # Selección de destinatarios
    # ------------------------------------------------------------------

    def _eligible_sockets(self, branch_ids: Iterable[Optional[int]]) -> Set[WebSocket]:
        """
        Conexiones con derecho a un evento de cualquiera de las sucursales indicadas.

        Devuelve un `set`, así que una conexión que califica por varios motivos (un admin
        durante una transferencia, por ejemplo) aparece UNA sola vez. De ahí viene la
        garantía de no duplicar notificaciones.
        """
        branch_list = list(branch_ids) or [None]
        targets: Set[WebSocket] = set()
        for websocket, ctx in self.connection_context.items():
            role = ctx.get("role")
            user_branch_id = ctx.get("branch_id")
            if any(
                can_receive_branch_event(role, user_branch_id, branch_id)
                for branch_id in branch_list
            ):
                targets.add(websocket)
        return targets

    def _forget(self, websocket: WebSocket) -> None:
        """Saca una conexión muerta de todas las estructuras."""
        self.connection_context.pop(websocket, None)
        for uid, sock_set in list(self.active_users.items()):
            sock_set.discard(websocket)
            if not sock_set:
                self.active_users.pop(uid, None)
        for bid, sock_set in list(self.branch_rooms.items()):
            sock_set.discard(websocket)
            if not sock_set:
                self.branch_rooms.pop(bid, None)

    async def _send_to(self, targets: Set[WebSocket], message: dict) -> int:
        """Envía a las conexiones dadas y limpia las que ya están muertas."""
        dead_sockets = set()
        text_data = json.dumps(message)
        delivered = 0
        for connection in targets:
            try:
                await connection.send_text(text_data)
                delivered += 1
            except Exception:
                dead_sockets.add(connection)
        for dead in dead_sockets:
            self._forget(dead)
        return delivered

    # ------------------------------------------------------------------
    # Difusión
    # ------------------------------------------------------------------

    async def send_personal_message(self, message: dict, user_id: int):
        """Envía a un usuario concreto. No necesita filtro de sucursal: el destinatario es
        explícito y el llamador ya decidió que le corresponde."""
        if user_id in self.active_users:
            await self._send_to(set(self.active_users[user_id]), message)

    async def broadcast_to_branch(self, branch_id: Optional[int], message: dict):
        """
        Emite un evento de la sucursal `branch_id` solo a quien tiene derecho a verlo:
        admins, supervisores globales y los usuarios de esa misma sucursal.

        Si `branch_id` es None (conversación sin sucursal asignada) llega únicamente a
        admins y supervisores globales. Antes esta rama emitía a TODOS los usuarios
        conectados, que es justo la fuga que este cambio corrige.
        """
        await self._send_to(self._eligible_sockets([branch_id]), message)

    async def broadcast_to_branches(self, branch_ids: Iterable[Optional[int]], message: dict):
        """
        Emite un evento que concierne a varias sucursales a la vez — el caso real es una
        transferencia, donde la sucursal anterior debe quitar la conversación de su bandeja
        y la nueva debe agregarla.

        Manda UN solo mensaje por conexión. Llamar `broadcast_to_branch` una vez por
        sucursal, como se hacía antes, entregaba el evento dos veces a los admins y
        supervisores globales, porque califican en ambas.
        """
        await self._send_to(self._eligible_sockets(branch_ids), message)

    async def broadcast_to_global_viewers(self, message: dict):
        """
        Emite solo a quien ve todas las sucursales (admins y supervisores globales).
        Para eventos administrativos que no pertenecen a ninguna sucursal en particular.
        """
        targets = {
            ws
            for ws, ctx in self.connection_context.items()
            if is_global_viewer(ctx.get("role"), ctx.get("branch_id"))
        }
        await self._send_to(targets, message)


ws_manager = ConnectionManager()
