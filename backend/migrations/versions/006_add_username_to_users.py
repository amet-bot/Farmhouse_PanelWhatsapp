"""add_username_to_users

Revision ID: 006_add_username_to_users
Revises: 005_add_delivery_and_payment
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '006_add_username_to_users'
down_revision: Union[str, None] = '005_add_delivery_and_payment'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Agregar columna username como nullable temporalmente
    op.add_column('users', sa.Column('username', sa.String(length=50), nullable=True))
    
    # 2. Migrar datos existentes: si username está vacío, usar prefijo de email o 'admin'
    op.execute(
        "UPDATE users SET username = CASE "
        "WHEN email LIKE 'admin%' THEN 'admin' "
        "ELSE SUBSTRING_INDEX(email, '@', 1) "
        "END WHERE username IS NULL OR username = ''"
    )

    # 3. Alterar username para que sea NOT NULL y crear índice único
    op.alter_column('users', 'username', existing_type=sa.String(length=50), nullable=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    # 4. Hacer email nullable
    op.alter_column('users', 'email', existing_type=sa.String(length=150), nullable=True)

def downgrade() -> None:
    op.alter_column('users', 'email', existing_type=sa.String(length=150), nullable=False)
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_column('users', 'username')