"""consumption_movements

El consumo registrado (Registro de consumo) descontaba la existencia pero no escribía en el libro
de movimientos, así que la comparación libro vs. fórmula (/inventory/movements/compare) siempre
diferenciaba en los insumos con consumo. Desde ahora cada consumo escribe su movimiento; esta
migración reconstruye los de los consumos que ya existían.

Revision ID: 068_consumption_movements
Revises: 067_item_photos
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "068_consumption_movements"
down_revision: Union[str, None] = "067_item_photos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not (inspector.has_table("inventory_movements") and inspector.has_table("consumption_items")):
        return
    # Solo los consumos que todavía no tienen su movimiento: correr dos veces no duplica.
    conn.execute(sa.text("""
        INSERT INTO inventory_movements
            (branch_id, inventory_item_id, movement_type, quantity, unit_cost, occurred_at,
             source_type, source_id, created_by_user_id, created_at)
        SELECT cr.branch_id, ci.inventory_item_id, 'consumption', -ci.quantity, ci.unit_cost, cr.occurred_at,
               'consumption', cr.id, cr.recorded_by_user_id, cr.created_at
        FROM consumption_items ci
        JOIN consumption_records cr ON cr.id = ci.consumption_record_id
        WHERE NOT EXISTS (
            SELECT 1 FROM inventory_movements m
            WHERE m.source_type = 'consumption' AND m.source_id = cr.id
        )
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM inventory_movements WHERE source_type = 'consumption'"))
