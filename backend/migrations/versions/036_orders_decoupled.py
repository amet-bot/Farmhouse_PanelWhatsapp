"""orders_decoupled

Farmhouse Link, Fase 3 (recortada): pedidos ya no dependen obligatoriamente de una conversación
de WhatsApp. `conversation_id` pasa a nullable (los pedidos actuales de WhatsApp y Menú Digital
siguen mandándolo, sin cambio de comportamiento). Se agregan `source` (marca de dónde vino el
pedido, con default 'whatsapp' para que las filas existentes queden marcadas correctamente sin
tocarlas una por una) y `external_reference` (nullable, único — idempotencia para pedidos que
vengan de una integración externa a futuro, mismo patrón que branch_id+invu_order_id de Invu).

Revision ID: 036_orders_decoupled
Revises: 035_invu_sales
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '036_orders_decoupled'
down_revision: Union[str, None] = '035_invu_sales'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c['name'] for c in inspector.get_columns('orders')}

    op.alter_column('orders', 'conversation_id', existing_type=sa.Integer(), nullable=True)

    if 'source' not in columns:
        op.add_column(
            'orders',
            sa.Column('source', sa.String(length=20), nullable=False, server_default='whatsapp'),
        )

    if 'external_reference' not in columns:
        op.add_column(
            'orders',
            sa.Column('external_reference', sa.String(length=150), nullable=True),
        )
        op.create_unique_constraint(
            'uq_orders_external_reference', 'orders', ['external_reference']
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c['name'] for c in inspector.get_columns('orders')}

    if 'external_reference' in columns:
        op.drop_constraint('uq_orders_external_reference', 'orders', type_='unique')
        op.drop_column('orders', 'external_reference')

    if 'source' in columns:
        op.drop_column('orders', 'source')

    op.alter_column('orders', 'conversation_id', existing_type=sa.Integer(), nullable=False)
