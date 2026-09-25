"""inventory_movements

Farmhouse Link, Fase 4 (primer bloque): libro de movimientos de inventario, en paralelo a la
fórmula que ya calcula existencia al vuelo (_on_hand_map). Se crea la tabla y se hace un backfill
de todo lo que ya existía en cargamentos, mermas y conteos, para no perder historia — la lectura
en vivo sigue siendo la fórmula de siempre, esto es solo la bitácora nueva escribiéndose desde
ahora en paralelo (ver routers/inventory.py).

Revision ID: 037_inventory_movements
Revises: 036_orders_decoupled
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '037_inventory_movements'
down_revision: Union[str, None] = '036_orders_decoupled'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('inventory_movements'):
        return

    op.create_table(
        'inventory_movements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('source_type', sa.String(length=20), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inventory_movements_id'), 'inventory_movements', ['id'], unique=False)
    op.create_index('ix_inv_movement_branch_item', 'inventory_movements', ['branch_id', 'inventory_item_id'], unique=False)
    op.create_index('ix_inv_movement_source', 'inventory_movements', ['source_type', 'source_id'], unique=False)

    # Backfill: reconstruir movimientos históricos a partir de lo que ya existe. La tabla se
    # acaba de crear en esta misma migración, así que no hay riesgo de duplicar si se corre de
    # nuevo por error (upgrade no es idempotente más allá de este punto, como el resto del
    # archivo — pero has_table ya cortó la salida arriba si la tabla existiera).
    conn.execute(sa.text("""
        INSERT INTO inventory_movements
            (branch_id, inventory_item_id, movement_type, quantity, unit_cost, occurred_at,
             source_type, source_id, created_by_user_id, created_at)
        SELECT s.branch_id, si.inventory_item_id, 'in', si.quantity, si.unit_cost, s.received_at,
               'shipment', s.id, s.received_by_user_id, s.received_at
        FROM shipment_items si
        JOIN shipments s ON s.id = si.shipment_id
    """))

    conn.execute(sa.text("""
        INSERT INTO inventory_movements
            (branch_id, inventory_item_id, movement_type, quantity, unit_cost, occurred_at,
             source_type, source_id, created_by_user_id, created_at)
        SELECT wr.branch_id, wi.inventory_item_id, 'out', -wi.quantity, wi.unit_cost, wr.occurred_at,
               'waste', wr.id, wr.recorded_by_user_id, wr.occurred_at
        FROM waste_items wi
        JOIN waste_records wr ON wr.id = wi.waste_record_id
    """))

    conn.execute(sa.text("""
        INSERT INTO inventory_movements
            (branch_id, inventory_item_id, movement_type, quantity, unit_cost, occurred_at,
             source_type, source_id, created_by_user_id, created_at)
        SELECT sc.branch_id, sci.inventory_item_id, 'adjustment', sci.difference, sci.unit_cost, sc.counted_at,
               'count', sc.id, sc.counted_by_user_id, sc.counted_at
        FROM stock_count_items sci
        JOIN stock_counts sc ON sc.id = sci.stock_count_id
        WHERE sci.difference <> 0
    """))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('inventory_movements'):
        op.drop_table('inventory_movements')
