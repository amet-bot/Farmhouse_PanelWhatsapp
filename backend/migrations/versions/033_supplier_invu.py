"""supplier_invu

Los proveedores pasan a venir de Invu POS. Siete columnas nullable sobre suppliers: el id de
Invu (que es la marca de origen) más los datos que allá existen y acá no.

Nada se borra ni se vuelve obligatorio: los proveedores cargados a mano siguen tal cual, con
invu_id en NULL, hasta que una sincronización los empareje por nombre.

Revision ID: 033_supplier_invu
Revises: 032_waste
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '033_supplier_invu'
down_revision: Union[str, None] = '032_waste'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = (
    ('invu_id', sa.Integer()),
    ('code', sa.String(length=50)),
    ('tax_id', sa.String(length=50)),
    ('contact_name', sa.String(length=150)),
    ('email', sa.String(length=150)),
    ('delivery_day', sa.Integer()),
    ('synced_at', sa.DateTime()),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('suppliers'):
        return

    existing = {c['name'] for c in inspector.get_columns('suppliers')}
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column('suppliers', sa.Column(name, type_, nullable=True))

    indices = {i['name'] for i in inspector.get_indexes('suppliers')}
    if 'ix_suppliers_invu_id' not in indices:
        # Único: un proveedor de Invu no puede quedar duplicado acá. NULL no cuenta para el
        # índice único, así que los cargados a mano conviven sin estorbar.
        op.create_index('ix_suppliers_invu_id', 'suppliers', ['invu_id'], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('suppliers'):
        return

    indices = {i['name'] for i in inspector.get_indexes('suppliers')}
    if 'ix_suppliers_invu_id' in indices:
        op.drop_index('ix_suppliers_invu_id', table_name='suppliers')

    existing = {c['name'] for c in inspector.get_columns('suppliers')}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column('suppliers', name)
