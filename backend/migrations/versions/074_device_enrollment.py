"""device_enrollment

Vinculación real de dispositivos: hasta ahora bastaba conocer el código público FH-DEVICE-…
(que la API misma listaba) para pasar como equipo autorizado. Ahora cada equipo guarda un token
secreto que solo se obtiene canjeando, una vez, un código de vinculación que genera el admin.

Los equipos ya registrados quedan SIN vincular: el administrador genera su código en
Administración → Dispositivos y alguien lo teclea en cada tablet/computadora.

Revision ID: 074_device_enrollment
Revises: 073_contract_staff_area
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "074_device_enrollment"
down_revision: Union[str, None] = "073_contract_staff_area"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("devices")}
    if "secret_hash" not in cols:
        op.add_column("devices", sa.Column("secret_hash", sa.String(64), nullable=True))
        op.create_index("ix_devices_secret_hash", "devices", ["secret_hash"], unique=True)
    if "enroll_code_hash" not in cols:
        op.add_column("devices", sa.Column("enroll_code_hash", sa.String(64), nullable=True))
    if "enroll_expires_at" not in cols:
        op.add_column("devices", sa.Column("enroll_expires_at", sa.DateTime(), nullable=True))
    if "enrolled_at" not in cols:
        op.add_column("devices", sa.Column("enrolled_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_devices_secret_hash", table_name="devices")
    op.drop_column("devices", "enrolled_at")
    op.drop_column("devices", "enroll_expires_at")
    op.drop_column("devices", "enroll_code_hash")
    op.drop_column("devices", "secret_hash")
