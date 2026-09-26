"""waste_photos

Evidencia de cada merma:
  - Fotos (waste_photos). La imagen se guarda en la base (MEDIUMBLOB, hasta 16 MB; el navegador
    la achica a ~300 KB antes de subirla) y no en el disco del servidor, que en Railway se pierde
    en cada deploy sin un volumen montado.
  - El peso leído en la balanza (waste_records.weight_value + weight_unit), aparte de la
    cantidad de cada insumo, que va en la unidad del insumo.

Revision ID: 044_waste_photos
Revises: 043_inventory_item_invu
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '044_waste_photos'
down_revision: Union[str, None] = '043_inventory_item_invu'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WEIGHT_COLUMNS = (
    ('weight_value', sa.Numeric(10, 3)),
    ('weight_unit', sa.String(length=5)),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('waste_records'):
        existing = {c['name'] for c in inspector.get_columns('waste_records')}
        for name, type_ in WEIGHT_COLUMNS:
            if name not in existing:
                op.add_column('waste_records', sa.Column(name, type_, nullable=True))

    if not inspector.has_table('waste_photos'):
        op.create_table(
            'waste_photos',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('waste_record_id', sa.Integer(), nullable=False),
            sa.Column('content_type', sa.String(length=40), nullable=False),
            sa.Column('size_bytes', sa.Integer(), nullable=False),
            sa.Column('data', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=False),
            sa.Column('uploaded_by_user_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['waste_record_id'], ['waste_records.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_waste_photos_id', 'waste_photos', ['id'])
        op.create_index('ix_waste_photos_waste_record_id', 'waste_photos', ['waste_record_id'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('waste_photos'):
        op.drop_index('ix_waste_photos_waste_record_id', table_name='waste_photos')
        op.drop_index('ix_waste_photos_id', table_name='waste_photos')
        op.drop_table('waste_photos')

    if inspector.has_table('waste_records'):
        existing = {c['name'] for c in inspector.get_columns('waste_records')}
        for name, _ in reversed(WEIGHT_COLUMNS):
            if name in existing:
                op.drop_column('waste_records', name)
