"""shipment_receiving

Recibir cargamentos contra factura:
  - shipments: invoice_number, has_issues, incident_id (la incidencia que se abrió sola).
  - shipment_items: invoiced_quantity (lo facturado), line_status (ok | falto | sobro |
    equivocado | danado) y line_note.
  - shipment_photos: foto de la factura o de lo que llegó mal.
  - expected_shipments: cargamentos agendados ("mañana llega PriceSmart, 3 pm").
Todo nullable o tabla nueva: lo ya cargado no cambia.

Revision ID: 050_shipment_receiving
Revises: 049_unit_cost_precision
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = '050_shipment_receiving'
down_revision: Union[str, None] = '049_unit_cost_precision'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ('shipments', 'invoice_number', sa.String(60)),
    ('shipments', 'has_issues', sa.Boolean()),
    ('shipments', 'incident_id', sa.Integer()),
    ('shipment_items', 'invoiced_quantity', sa.Numeric(10, 3)),
    ('shipment_items', 'line_status', sa.String(20)),
    ('shipment_items', 'line_note', sa.String(200)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, name, type_ in COLUMNS:
        if inspector.has_table(table) and name not in {c['name'] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(name, type_, nullable=True))

    if not inspector.has_table('shipment_photos'):
        op.create_table(
            'shipment_photos',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('shipments.id', ondelete='CASCADE'), nullable=False),
            sa.Column('content_type', sa.String(40), nullable=False),
            sa.Column('size_bytes', sa.Integer(), nullable=False),
            sa.Column('data', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=False),
            sa.Column('uploaded_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_shipment_photos_id', 'shipment_photos', ['id'])
        op.create_index('ix_shipment_photos_shipment_id', 'shipment_photos', ['shipment_id'])

    if not inspector.has_table('expected_shipments'):
        op.create_table(
            'expected_shipments',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('branch_id', sa.Integer(), sa.ForeignKey('branches.id'), nullable=False),
            sa.Column('supplier_id', sa.Integer(), sa.ForeignKey('suppliers.id'), nullable=True),
            sa.Column('expected_date', sa.Date(), nullable=False),
            sa.Column('time_from', sa.String(5), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='pendiente'),
            sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('shipment_id', sa.Integer(), sa.ForeignKey('shipments.id', ondelete='SET NULL'), nullable=True),
            sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_expected_shipments_id', 'expected_shipments', ['id'])
        op.create_index('ix_expected_shipment_branch_status', 'expected_shipments', ['branch_id', 'status'])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('expected_shipments'):
        op.drop_table('expected_shipments')
    if inspector.has_table('shipment_photos'):
        op.drop_table('shipment_photos')
    for table, name, _ in reversed(COLUMNS):
        if inspector.has_table(table) and name in {c['name'] for c in inspector.get_columns(table)}:
            op.drop_column(table, name)
