"""audit_events

Farmhouse Link, Fase 4 (cuarto bloque): bitácora de auditoría — quién hizo qué y cuándo, en las
escrituras que importa poder rastrear (usuarios/permisos, transferencias, mermas, conteos,
cargamentos).

Revision ID: 040_audit_events
Revises: 039_ops_requests_incidents_tasks
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '040_audit_events'
down_revision: Union[str, None] = '039_ops_requests_incidents_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('audit_events'):
        return

    op.create_table(
        'audit_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('branch_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_audit_events_id'), 'audit_events', ['id'], unique=False)
    op.create_index('ix_audit_entity', 'audit_events', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_branch_created', 'audit_events', ['branch_id', 'created_at'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('audit_events'):
        op.drop_table('audit_events')
