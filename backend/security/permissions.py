"""
Catálogo de permisos por capacidad (Fase 2 del plan Farmhouse Link).

`User.role` ("agent"/"supervisor"/"admin") sigue siendo la fuente de compatibilidad y de alcance
por sucursal — eso lo resuelve `security/access_control.py` y no cambia acá. Este catálogo es una
capa aparte, ortogonal: "¿existe esta capacidad para este rol?", nunca "¿en qué sucursal?".

Se mantiene como un mapeo estático en código, no una tabla en base de datos: hoy los roles mismos
tampoco viven en una tabla (son un string en la columna `role`), así que una tabla de permisos
editable en caliente sin que los roles lo sean sería inconsistente y más complejidad de la que
hace falta todavía. Si el día de mañana los roles pasan a ser configurables, este catálogo se
puede migrar a tabla en el mismo movimiento.
"""
from typing import Set

from fastapi import Depends, HTTPException, status

from models.user import User
from security.auth import get_current_user

# Catálogo completo (calcado de la sección 6 del plan grande). Cada código es "dominio.accion".
PERMISSIONS: Set[str] = {
    "dashboard.view",
    "attention.view", "attention.reply", "attention.assign", "attention.transfer",
    "customers.view", "customers.edit",
    "orders.view", "orders.create", "orders.update",
    "inventory.view", "inventory.receive", "inventory.count", "inventory.record_waste", "inventory.adjust", "inventory.transfer",
    "purchasing.view", "purchasing.request", "purchasing.approve",
    "internal_chat.use",
    "reports.view",
    "users.manage",
    "devices.manage",
    "integrations.manage",
}

_AGENT_PERMISSIONS: Set[str] = {
    "dashboard.view",
    "attention.view", "attention.reply", "attention.assign", "attention.transfer",
    "customers.view",
    "orders.view", "orders.create", "orders.update",
    "inventory.view", "inventory.receive", "inventory.count", "inventory.record_waste",
    "purchasing.view", "purchasing.request",
    "internal_chat.use",
}

_SUPERVISOR_PERMISSIONS: Set[str] = _AGENT_PERMISSIONS | {
    "customers.edit",
    "inventory.adjust", "inventory.transfer",
    "purchasing.approve",
    "reports.view",
}

# Admin: todo el catálogo, incluido lo que todavía no tiene dueño claro (users.manage,
# devices.manage, integrations.manage) — mismo criterio que ya usan hoy los `require_role(["admin"])`.
_ADMIN_PERMISSIONS: Set[str] = set(PERMISSIONS)

ROLE_PERMISSIONS: dict[str, Set[str]] = {
    "agent": _AGENT_PERMISSIONS,
    "supervisor": _SUPERVISOR_PERMISSIONS,
    "admin": _ADMIN_PERMISSIONS,
}


def resolve_permissions(role: str) -> Set[str]:
    """Los permisos que tiene un rol. Un rol desconocido no tiene ninguno (nunca falla abierto)."""
    return ROLE_PERMISSIONS.get(role, set())


def has_permission(user: User, permission: str) -> bool:
    return permission in resolve_permissions(user.role)


def require_permission(permission: str):
    """Dependencia FastAPI — mismo patrón que `require_role` en security/auth.py."""
    def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso denegado. Se requiere la capacidad '{permission}'."
            )
        return current_user
    return permission_checker
