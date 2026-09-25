"""prep_checklists

Prep por estación (Bowls), por sucursal: plantilla de ítems con par objetivo, y un checklist que
se llena varias veces al día por checkpoint (los checkpoints son configurables por plantilla, no
fijos, para cubrir tanto "10am/3pm/8pm" como "Congelador Grande/chico").

Revision ID: 041_prep_checklists
Revises: 040_audit_events
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '041_prep_checklists'
down_revision: Union[str, None] = '040_audit_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('prep_templates'):
        op.create_table(
            'prep_templates',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False, server_default='Bowls'),
            sa.Column('checkpoints_json', sa.Text(), nullable=False),
            sa.Column('active', sa.String(length=1), nullable=False, server_default='Y'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_prep_templates_id'), 'prep_templates', ['id'], unique=False)
        op.create_index('ix_prep_template_branch', 'prep_templates', ['branch_id'], unique=False)

    if not inspector.has_table('prep_template_items'):
        op.create_table(
            'prep_template_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('template_id', sa.Integer(), nullable=False),
            sa.Column('section', sa.String(length=60), nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('unit_label', sa.String(length=60), nullable=True),
            sa.Column('par_target', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
            sa.ForeignKeyConstraint(['template_id'], ['prep_templates.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_prep_template_items_id'), 'prep_template_items', ['id'], unique=False)
        op.create_index('ix_prep_item_template', 'prep_template_items', ['template_id'], unique=False)

    if not inspector.has_table('prep_checks'):
        op.create_table(
            'prep_checks',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('template_id', sa.Integer(), nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('checkpoint', sa.String(length=60), nullable=False),
            sa.Column('check_date', sa.Date(), nullable=False),
            sa.Column('filled_by_user_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['template_id'], ['prep_templates.id']),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['filled_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('template_id', 'checkpoint', 'check_date', name='uq_prep_check_slot'),
        )
        op.create_index(op.f('ix_prep_checks_id'), 'prep_checks', ['id'], unique=False)
        op.create_index('ix_prep_check_branch_date', 'prep_checks', ['branch_id', 'check_date'], unique=False)

    if not inspector.has_table('prep_check_entries'):
        op.create_table(
            'prep_check_entries',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('check_id', sa.Integer(), nullable=False),
            sa.Column('template_item_id', sa.Integer(), nullable=False),
            sa.Column('on_hand', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('note', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['check_id'], ['prep_checks.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['template_item_id'], ['prep_template_items.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('check_id', 'template_item_id', name='uq_prep_entry_item'),
        )
        op.create_index(op.f('ix_prep_check_entries_id'), 'prep_check_entries', ['id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('prep_check_entries', 'prep_checks', 'prep_template_items', 'prep_templates'):
        if inspector.has_table(table):
            op.drop_table(table)
