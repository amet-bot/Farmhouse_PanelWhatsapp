"""add_suppliers

Revision ID: 029_add_suppliers
Revises: 028_inventory_cargamentos
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '029_add_suppliers'
down_revision: Union[str, None] = '028_inventory_cargamentos'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('suppliers'):
        op.create_table(
            'suppliers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('phone', sa.String(length=30), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    existing_idx = {i['name'] for i in sa.inspect(conn).get_indexes('suppliers')}
    if op.f('ix_suppliers_id') not in existing_idx:
        op.create_index(op.f('ix_suppliers_id'), 'suppliers', ['id'], unique=False)
    if op.f('ix_suppliers_name') not in existing_idx:
        op.create_index(op.f('ix_suppliers_name'), 'suppliers', ['name'], unique=True)

    shipment_columns = {c['name'] for c in inspector.get_columns('shipments')}

    if 'supplier_id' not in shipment_columns:
        op.add_column('shipments', sa.Column('supplier_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_shipments_supplier_id', 'shipments', 'suppliers', ['supplier_id'], ['id']
        )

    # Migra cualquier cargamento ya cargado con el proveedor como texto libre (columna vieja
    # `supplier`) a un Supplier real del catálogo nuevo, antes de borrar esa columna.
    if 'supplier' in shipment_columns:
        rows = conn.execute(sa.text(
            "SELECT DISTINCT supplier FROM shipments WHERE supplier IS NOT NULL AND supplier != ''"
        )).fetchall()
        for (supplier_name,) in rows:
            existing = conn.execute(
                sa.text("SELECT id FROM suppliers WHERE name = :name"), {"name": supplier_name}
            ).fetchone()
            if existing:
                supplier_id = existing[0]
            else:
                conn.execute(
                    sa.text("INSERT INTO suppliers (name, active, created_at) VALUES (:name, 1, CURRENT_TIMESTAMP)"),
                    {"name": supplier_name}
                )
                supplier_id = conn.execute(
                    sa.text("SELECT id FROM suppliers WHERE name = :name"), {"name": supplier_name}
                ).fetchone()[0]
            conn.execute(
                sa.text("UPDATE shipments SET supplier_id = :sid WHERE supplier = :name"),
                {"sid": supplier_id, "name": supplier_name}
            )

        op.drop_column('shipments', 'supplier')


def downgrade() -> None:
    conn = op.get_bind()
    op.add_column('shipments', sa.Column('supplier', sa.String(length=150), nullable=True))
    conn.execute(sa.text(
        "UPDATE shipments s JOIN suppliers sup ON sup.id = s.supplier_id SET s.supplier = sup.name"
    ))
    op.drop_constraint('fk_shipments_supplier_id', 'shipments', type_='foreignkey')
    op.drop_column('shipments', 'supplier_id')
    op.drop_table('suppliers')
