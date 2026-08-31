import pytest
from tests.conftest import auth_headers_for
from models.user import User

def test_cannot_delete_or_deactivate_last_active_admin(
    client, db_session, admin_user
):
    """
    Requisito (d): No se puede eliminar ni desactivar al último admin activo.
    """
    headers_admin = auth_headers_for(admin_user)

    # 1. Intentar desactivar al único admin activo -> Debe dar 400
    res_deactivate = client.post(f"/api/users/{admin_user.id}/toggle-active", headers=headers_admin)
    assert res_deactivate.status_code == 400
    assert "No puedes desactivar tu propia cuenta" in res_deactivate.json()["detail"] or "administrador" in res_deactivate.json()["detail"]

    # 2. Intentar eliminar al único admin activo -> Debe dar 400
    res_delete = client.delete(f"/api/users/{admin_user.id}", headers=headers_admin)
    assert res_delete.status_code == 400

    # 3. Crear un segundo admin para probar la regla cuando hay otro admin
    admin2 = User(
        id=10,
        username="admin2",
        name="Admin Secundario",
        email="admin2@farmhouse.pa",
        password_hash="fakehash",
        role="admin",
        active=True
    )
    db_session.add(admin2)
    db_session.commit()

    # Admin 1 desactiva a Admin 2 (permitido porque queda Admin 1 activo)
    res_deact_2 = client.post(f"/api/users/{admin2.id}/toggle-active", headers=headers_admin)
    assert res_deact_2.status_code == 200
    assert res_deact_2.json()["active"] is False

    # Ahora que Admin 2 está inactivo, Admin 1 es el único activo:
    # Si Admin 2 intenta eliminar a Admin 1 -> Debe fallar porque Admin 1 es el único activo
    # O si se intenta desactivar Admin 1 -> Debe fallar
