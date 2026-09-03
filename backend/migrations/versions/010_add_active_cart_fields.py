"""add_active_cart_fields

Revision ID: 010_add_active_cart_fields
Revises: 009_add_push_subscriptions
Create Date: 2026-09-03 00:00:00.000000

Agrega updated_at/expires_at a `orders` para soportar el carrito activo del Menú
Digital (status="carrito_activo"), que reutiliza la tabla `orders` existente en
vez de crear una entidad nueva.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '010_add_active_cart_fields'
down_revision: Union[str, None] = '009_add_push_subscriptions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in _cols(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing('orders', sa.Column('updated_at', sa.DateTime(), nullable=True))
    _add_column_if_missing('orders', sa.Column('expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    if 'expires_at' in _cols('orders'):
        op.drop_column('orders', 'expires_at')
    if 'updated_at' in _cols('orders'):
        op.drop_column('orders', 'updated_at')
