"""fix_devices_status_default

Revision ID: 008_fix_devices_status_default
Revises: 007_hardening_security_and_orders
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '008_fix_devices_status_default'
down_revision: Union[str, None] = '007_hardening_security_and_orders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # El valor por defecto en MySQL era 'offline', pero el modelo y todos los
    # endpoints de creación de dispositivos siempre asumen 'active'. Se alinea
    # el server_default con el comportamiento real de la aplicación para que
    # un INSERT que omita 'status' no cree dispositivos inconsistentes.
    op.alter_column(
        'devices', 'status',
        existing_type=sa.String(length=50),
        nullable=False,
        server_default='active'
    )

def downgrade() -> None:
    op.alter_column(
        'devices', 'status',
        existing_type=sa.String(length=50),
        nullable=False,
        server_default='offline'
    )
