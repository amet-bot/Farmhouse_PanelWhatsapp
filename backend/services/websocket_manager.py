import json
from typing import Dict, Set
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Mapeo: user_id -> Set[WebSocket]
        self.active_users: Dict[int, Set[WebSocket]] = {}
        # Mapeo: branch_id -> Set[WebSocket]
        self.branch_rooms: Dict[int, Set[WebSocket]] = {}
        # Supervisores y administradores conectados
        self.supervisor_room: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, user_id: int, branch_id: int = None, role: str = "agent"):
        # El accept() ya se hace al inicio de websocket_endpoint (routers/websocket.py),
        # antes de validar credenciales, para que un close(code=...) por auth fallida
        # pueda transmitir su código real al cliente.

        # 1. Registrar usuario
        if user_id not in self.active_users:
            self.active_users[user_id] = set()
        self.active_users[user_id].add(websocket)

        # 2. Registrar en sala de sucursal
        if branch_id:
            if branch_id not in self.branch_rooms:
                self.branch_rooms[branch_id] = set()
            self.branch_rooms[branch_id].add(websocket)

        # 3. Registrar en sala de supervisores si aplica
        if role in ["supervisor", "admin"]:
            self.supervisor_room.add(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int, branch_id: int = None, role: str = "agent"):
        if user_id in self.active_users and websocket in self.active_users[user_id]:
            self.active_users[user_id].remove(websocket)
            if not self.active_users[user_id]:
                del self.active_users[user_id]

        if branch_id and branch_id in self.branch_rooms and websocket in self.branch_rooms[branch_id]:
            self.branch_rooms[branch_id].remove(websocket)
            if not self.branch_rooms[branch_id]:
                del self.branch_rooms[branch_id]

        if websocket in self.supervisor_room:
            self.supervisor_room.remove(websocket)

    async def send_personal_message(self, message: dict, user_id: int):
        if user_id in self.active_users:
            dead_sockets = set()
            text_data = json.dumps(message)
            for connection in self.active_users[user_id]:
                try:
                    await connection.send_text(text_data)
                except Exception:
                    dead_sockets.add(connection)
            for dead in dead_sockets:
                self.active_users[user_id].discard(dead)

    async def broadcast_to_branch(self, branch_id: int, message: dict):
        """
        Emite a los agentes de la sucursal indicada Y a todos los supervisores/admins conectados.
        Si branch_id es None (conversación sin sucursal asignada), emite a todos los usuarios
        conectados para que la entrada de nuevas conversaciones sea visible en tiempo real.
        """
        targets: Set[WebSocket] = set()
        
        if branch_id and branch_id in self.branch_rooms:
            targets.update(self.branch_rooms[branch_id])
            
        targets.update(self.supervisor_room)

        # Si no tiene sucursal asignada aún, notificar a todos los usuarios conectados
        if not branch_id:
            for sock_set in self.active_users.values():
                targets.update(sock_set)

        dead_sockets = set()
        text_data = json.dumps(message)
        for connection in targets:
            try:
                await connection.send_text(text_data)
            except Exception:
                dead_sockets.add(connection)

    async def broadcast_to_supervisors(self, message: dict):
        dead_sockets = set()
        text_data = json.dumps(message)
        for connection in self.supervisor_room:
            try:
                await connection.send_text(text_data)
            except Exception:
                dead_sockets.add(connection)

    async def broadcast_all(self, message: dict):
        """
        Emite un mensaje a TODOS los clientes WebSocket activos en el sistema (todas las sucursales,
        agentes, supervisores y administradores). Usado para eventos globales como la
        eliminación en tiempo real de una conversación.
        """
        targets: Set[WebSocket] = set()
        for sock_set in self.active_users.values():
            targets.update(sock_set)

        dead_sockets = set()
        text_data = json.dumps(message)
        for connection in targets:
            try:
                await connection.send_text(text_data)
            except Exception:
                dead_sockets.add(connection)

        for dead in dead_sockets:
            for uid, sock_set in list(self.active_users.items()):
                sock_set.discard(dead)
                if not sock_set:
                    self.active_users.pop(uid, None)
            for bid, sock_set in list(self.branch_rooms.items()):
                sock_set.discard(dead)
                if not sock_set:
                    self.branch_rooms.pop(bid, None)
            self.supervisor_room.discard(dead)

ws_manager = ConnectionManager()
