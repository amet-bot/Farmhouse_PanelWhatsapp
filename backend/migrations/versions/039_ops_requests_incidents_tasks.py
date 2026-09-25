"""ops_requests_incidents_tasks

Farmhouse Link, Fase 4 (tercer bloque): Operación de Sucursal — solicitudes de insumos,
incidencias y tareas. Tres tablas independientes, cada una con su propio ciclo de vida simple;
sin motor de flujos genérico.

Revision ID: 039_ops_requests_incidents_tasks
Revises: 038_transfers
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '039_ops_requests_incidents_tasks'
down_revision: Union[str, None] = '038_transfers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('supply_requests'):
        op.create_table(
            'supply_requests',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('requested_by_user_id', sa.Integer(), nullable=False),
            sa.Column('item_name', sa.String(length=150), nullable=False),
            sa.Column('quantity_hint', sa.String(length=50), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='open'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('resolved_by_user_id', sa.Integer(), nullable=True),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['resolved_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_supply_requests_id'), 'supply_requests', ['id'], unique=False)
        op.create_index('ix_supply_request_branch_status', 'supply_requests', ['branch_id', 'status'], unique=False)

    if not inspector.has_table('incidents'):
        op.create_table(
            'incidents',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('reported_by_user_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=150), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('severity', sa.String(length=20), nullable=False, server_default='media'),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='abierta'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('resolved_by_user_id', sa.Integer(), nullable=True),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('resolution_notes', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['reported_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['resolved_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_incidents_id'), 'incidents', ['id'], unique=False)
        op.create_index('ix_incident_branch_status', 'incidents', ['branch_id', 'status'], unique=False)

    if not inspector.has_table('tasks'):
        op.create_table(
            'tasks',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('created_by_user_id', sa.Integer(), nullable=False),
            sa.Column('assigned_to_user_id', sa.Integer(), nullable=True),
            sa.Column('title', sa.String(length=150), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='pendiente'),
            sa.Column('due_date', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['assigned_to_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_tasks_id'), 'tasks', ['id'], unique=False)
        op.create_index('ix_task_branch_status', 'tasks', ['branch_id', 'status'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('tasks', 'incidents', 'supply_requests'):
        if inspector.has_table(table):
            op.drop_table(table)
