"""inventory_item_invu

Los insumos pasan a poder venir de Invu POS (sus "Ingredientes"), igual que los proveedores en
033_supplier_invu. Cinco columnas nullable sobre inventory_items: el id de Invu (marca de origen),
su código (P204...), el tipo (materia prima o preparación de la casa), el costo de referencia que
Invu tiene cargado y cuándo se sincronizó.

Nada se borra ni se vuelve obligatorio: los insumos cargados a mano siguen tal cual, con invu_id
en NULL, hasta que una sincronización los empareje por nombre. Y se siguen pudiendo crear a mano.

Cuelga de 041 (lo último en producción). La 042 de Comunicación Interna que está sin commitear
tiene que pasar a colgar de ESTA (down_revision = '043_inventory_item_invu') antes de subirse: si
no, quedan dos cabezas y `alembic upgrade head` del arranque falla.

Revision ID: 043_inventory_item_invu
Revises: 041_prep_checklists
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '043_inventory_item_invu'
down_revision: Union[str, None] = '041_prep_checklists'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = (
    ('invu_id', sa.Integer()),
    ('code', sa.String(length=50)),
    ('kind', sa.String(length=20)),
    ('reference_cost', sa.Numeric(12, 4)),
    ('synced_at', sa.DateTime()),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('inventory_items'):
        return

    existing = {c['name'] for c in inspector.get_columns('inventory_items')}
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column('inventory_items', sa.Column(name, type_, nullable=True))

    indices = {i['name'] for i in inspector.get_indexes('inventory_items')}
    if 'ix_inventory_items_invu_id' not in indices:
        # Único: un ingrediente de Invu no puede quedar duplicado acá. NULL no cuenta para el
        # índice único, así que los cargados a mano conviven sin estorbar.
        op.create_index('ix_inventory_items_invu_id', 'inventory_items', ['invu_id'], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('inventory_items'):
        return

    indices = {i['name'] for i in inspector.get_indexes('inventory_items')}
    if 'ix_inventory_items_invu_id' in indices:
        op.drop_index('ix_inventory_items_invu_id', table_name='inventory_items')

    existing = {c['name'] for c in inspector.get_columns('inventory_items')}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column('inventory_items', name)
